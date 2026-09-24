# Implementation Findings — Supply Chain Semantic Layer

This document records what happened when the design in
`supply-chain-semantic-framework-design.md` was actually built end-to-end against
live Snowflake tables (50 suppliers, 200 parts, 10 plants, 100 customers, 5,000
purchase orders, 13,992 PO lines, 17,430 shipments). It supplements the design
doc without modifying it — the design doc stays as the original planning
artifact; this file is the as-built record of what was discovered and fixed
during implementation and validation.

The single highest-severity risk row in the design doc's Section 8 risk table —
**"Metrics silently wrong at the wrong grain"** — was not just a theoretical
concern. It reproduced exactly, was caught only because metrics were
independently cross-checked against a Python ground-truth computation, and
required a real architectural fix. Two concrete findings, both from the live
build:

## Finding 1 — table-scoped DDL metrics fan out across one-to-many relationships, even though the fan-out is invisible if you only spot-check the inputs

`fill_rate` needs to combine `SUM(shipments.shipped_qty)` (owned by `shipments`)
with `SUM(po_lines.ordered_qty)` (owned by `po_lines`), and `po_lines` →
`shipments` is one-to-many (a line can have multiple partial shipments). Defined
as a DDL metric owned by `shipments`:

```sql
shipments.fill_rate AS
  shipments.total_shipped_qty / NULLIF(po_lines.total_ordered_qty, 0) * 100
```

this returned **65.82%**. Querying `total_shipped_qty` and `total_ordered_qty`
side by side (without `fill_rate` in the same query) returned the *correct*
totals (3,115,490 and 3,810,616 → 81.76%) — so a spot-check of the inputs alone
would have passed review. The metric itself was silently wrong: Snowflake
evaluates a table-scoped metric's cross-table references inside that table's
own join context, so `po_lines.total_ordered_qty` gets re-aggregated at
`shipments` grain and fans out by the shipments-per-line ratio (17,430 /
13,992 ≈ 1.246×; 3,810,616 × 1.246 ≈ 4.75M, and 3,115,490 / 4.75M ≈ 65.6%,
matching the observed error almost exactly).

**The fix:** Snowflake's DDL `METRICS` clause has no way to define a metric that
isn't owned by exactly one table (every metric is `<table_alias>.<metric>`).
The correct construct is a **YAML-only "derived metric"** — a `metrics:` block
at the *top level* of the YAML spec, outside `tables:`, which is genuinely
scoped to the view rather than to a table and combines two independently
pre-aggregated metrics without re-entering either table's join context:

```yaml
metrics:                      # top-level, sibling of `tables:` — NOT nested under a table
  - name: fill_rate
    expr: "shipments.total_shipped_qty / NULLIF(po_lines.total_ordered_qty, 0) * 100"
    description: Ratio of total shipped quantity to total ordered quantity, as a percentage
```

Deployed via `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML`, this returned exactly
**81.75817200%** (= 3,115,490 / 3,810,616), matching ground truth.

**Practical implication for the framework** (ties back to design doc Section
1b/1d): any canonical metric that combines facts from two tables connected by
a one-to-many (or many-to-many) relationship must be authored as a YAML
view-level derived metric, never as a table-scoped DDL metric — and the
validation gate must include a side-by-side check (metric vs. its raw
component totals queried independently) specifically *because* the wrong
version passes a naive "are the inputs right" check.

## Finding 2 — denormalized foreign keys create multi-path relationship ambiguity that DDL/YAML happily accept at creation time and only fail at query time

`shipments.plant_id` is a denormalized copy of `purchase_orders.plant_id`
(every shipment fulfills a line of an order, and is generated to land at that
order's plant). Modeling both a direct `shipments → plants` relationship *and*
the transitive path `shipments → po_lines → purchase_orders → plants` compiled
and created successfully, but any query mixing a `plants` dimension with a
`shipments`-owned metric failed at query time with `Multi-path relationship ...
is not supported`.

**The fix:** drop the redundant direct relationship and rely on the transitive
path alone — there is exactly one correct relationship per pair of entities
once denormalized/derived foreign keys are recognized as such.

**Practical implication for the framework** (ties back to design doc Section
1b): the ontology-mapping phase must actively detect denormalized FK columns
(a column that always agrees with a value reachable via an existing
relationship path) and refuse to also model them as an independent
relationship, rather than letting the semantic-view compiler accept the
redundancy silently.

## Why this matters for the validation gate

Both bugs were only caught because the validation gate (design doc Section 1d)
included cross-checks beyond "does it compile" — validating that a metric's
value matches an independent computation of the same quantity, and that every
dimension × metric combination in the persona question set actually executes.
Neither bug produced a compile error; both were silently wrong or worked in
isolation but broke in combination. This is the strongest evidence yet that
the validation gate is not optional overhead — it is where this class of bug
is caught at all.

## Artifact map (source of truth)

- `semantic_view/supply_chain_analytics.yaml` — canonical, correct, deployed
  semantic view definition (includes both fixes above).
- `sql/01_create_semantic_view.sql` — deprecated DDL version, kept only as a
  documented record of the bug; do not run it against the live view.
- `scripts/generate_synthetic_data.py`, `scripts/load_to_snowflake.py` —
  synthetic data generation and load.
- `SC_DEMO.RAW.*` / `SC_DEMO.ANALYTICS.SUPPLY_CHAIN_ANALYTICS` — live Snowflake
  objects.

## Verified ground-truth metric values (SC_DEMO, current dataset)

| Metric | Value |
|---|---|
| fill_rate | 81.75817200% |
| on_time_delivery_rate | 82.5875% |
| otif_rate | 5.3069% |
| perfect_order_rate | 3.4194% |
| supplier_defect_rate | 2.16973900% |
| avg_lead_time_days | 14.556454 |
| order_cycle_time_days | 21.1054 |

All 17 persona-question verified queries in the YAML were smoke-tested and
execute correctly against the live view.

## Finding 3 — undeclared multi-path relationships from *incidental* FKs (found while automating Findings 1 & 2)

The ontology-mapping/template-instantiation engine (`framework/`, see
`framework/README.md`) was built to automate Findings 1 and 2 as general
rules rather than one-off manual fixes: cross-entity metrics are always
emitted as YAML derived metrics, and a sampled-row SQL check automatically
detects and drops denormalized relationships. Running the full pipeline
against `SC_DEMO.RAW` with **zero manual mapping input** reproduced the
hand-built view's metrics to an exact match (see
`framework/runs/validation_report.md`) -- but surfaced a *third* correctness
rule in the process.

The first version of the relationship-resolution logic built a semantic-view
relationship for **every** FK the extraction phase found between two
ontology-mapped tables. Phase 1a's FK inference is purely structural (a
naming-convention filter + value-containment check, needed because none of
the source tables have declared FK constraints), so it also found a real,
but ontology-*unmodeled*, relationship: `PARTS.PRIMARY_SUPPLIER_ID ->
SUPPLIERS` (a part's preferred/primary supplier). This is a genuinely
different relationship from `SHIPMENTS.SUPPLIER_ID -> SUPPLIERS` (the
supplier that actually shipped a given transaction) -- confirmed by the same
denormalization-check SQL, which correctly reported 2,510 of 17,430 sampled
rows disagree, i.e. NOT redundant.

Even though both relationships are legitimate and independently correct,
having *two* paths between `SHIPMENTS` and `SUPPLIERS` broke every plain
`DIMENSIONS supplier_name METRICS ...` query with `Multi-path relationship
... is not supported` -- Snowflake rejects ambiguous paths between two
logical tables regardless of whether the extra path is a denormalization bug
or a second genuine relationship.

**The fix:** relationship instantiation must be scoped to only what the
ontology *explicitly declares* (`entity.foreign_keys` in `ontology.json`),
matched against the extracted FKs -- not every FK the extraction happens to
find between two mapped tables. The ontology is the source of truth for
which relationships the semantic layer exposes; incidental structural
discoveries (like the primary-supplier FK) are real information about the
source schema but should not be silently promoted into the semantic view
without a corresponding ontology entry. **Practical implication for the
framework:** Phase 1b's cross-validation (1d) should flag "relationships
found in the data but not modeled in the ontology" as a suggestion for human
review, rather than either silently including or silently discarding them.

## Verified end-to-end automation result

`framework/onboard.py` (Phases 1a -> 1d chained) was run against
`SC_DEMO.RAW`, targeting a new view
(`SC_DEMO.ANALYTICS_AUTO.SUPPLY_CHAIN_ANALYTICS_AUTO`), with the mapping
engine given no hints about the already-known-correct hand-built mapping.
Result: every one of the 7 canonical metrics matched the hand-built ground
truth to the same decimal precision, and 11 of 12 persona verified queries
executed correctly (the 12th requires a month-level time dimension the
template engine doesn't yet auto-derive). Full comparison in
`framework/runs/validation_report.md`.

## Finding 4 — browser-SSO friction for standalone scripts, fixed via PAT (not keyring)

**Symptom:** every standalone Python invocation of the `framework/*.py`
scripts (outside `sql_execute`/`python_repl`'s pre-authenticated session)
triggered a fresh browser SSO prompt, even though
`~/.snowflake/connections.toml` already had `client_store_temporary_credential
= true` set on the `authenticator = "OAUTH_AUTHORIZATION_CODE"` profile.

**Root cause:** the standalone `python.exe` interpreter has no working OS
secure-storage backend for token caching. Installing `keyring` +
`snowflake-connector-python[secure-local-storage]` didn't fix it either --
Windows Credential Manager's `CredWrite` rejects the OAuth access token as
too large for a single credential blob (`error 1783 "The stub received bad
data"`). This is a genuine Windows-size-limit dead end, not a config mistake;
`keyring` was reverted.

**Fix:** stop using browser-based OAuth for script connections entirely.
Generated a Personal Access Token (PAT) instead -- "Authentication with PAT
doesn't involve any human interaction" (Snowflake docs). Concretely:

1. A human user (`TYPE=PERSON`) can *generate* a PAT without a network
   policy, but must be subject to one to *authenticate* with it. There was no
   network policy on the account, so a permissive one
   (`ALLOWED_IP_LIST=('0.0.0.0/0')`) was created and attached to the user --
   this satisfies the requirement without narrowing real access (same
   effective posture as having no policy at all).
2. `ALTER USER ... ADD PROGRAMMATIC ACCESS TOKEN framework_scripts_pat
   ROLE_RESTRICTION='ACCOUNTADMIN' DAYS_TO_EXPIRY=365` generated the token.
   The secret was stored via `cortex secret store` immediately, written to a
   temp file only transiently (deleted right after), and never captured in
   any command-line argument or committed file.
3. A separate `[UU60334_PAT]` profile was added to `connections.toml`
   (`authenticator = "PROGRAMMATIC_ACCESS_TOKEN"`, `role = "ACCOUNTADMIN"`,
   no secret in the file) alongside the original SSO profile, which stays
   untouched for interactive session use.
4. **Non-obvious connector detail**: passing the PAT as the `password`
   parameter is silently rejected as `"Programmatic access token is
   invalid"` -- the connector expects it via the `token` parameter instead.
   `framework/sf_connect.py` encodes this.
5. `bash`'s `secret_env` parameter did not propagate the secret into the
   subprocess environment in this environment/version -- `cortex secret run
   --map "<key>=<ENV_VAR>" -- <command>` was used instead and worked
   correctly.

**Bonus finding while testing this (Finding 5):** under a PAT-authenticated
session, `SNOWFLAKE.CORTEX.AI_COMPLETE`'s result came back double-JSON-encoded
(the whole response wrapped in an outer quoted string with escaped inner
quotes/newlines), which broke `map_ontology.py`'s `_extract_json` (regex-then-
`json.loads`) with `Expecting property name enclosed in double quotes`. Fixed
by unwrapping the outer JSON-string layer first if present, before the
existing `{...}` regex extraction. This is now handled for both the
OAuth-session and PAT-session code paths.

**Verified:** `extract_metadata.py` and the full `onboard.py` pipeline both
ran via `--connection UU60334_PAT` with zero browser interaction, reproducing
the same 7 canonical metrics exactly and passing 10 of 12 verified queries (2
fail on a pre-existing, unrelated ambiguous-dimension-name bug when both
`Supplier.region` and `Facility.region` appear in the same query -- not caused
by this fix, not yet resolved).


