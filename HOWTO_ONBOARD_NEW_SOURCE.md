# How to Onboard a New Data Source

This is the step-by-step guide for adding a new source schema to the governed
semantic layer -- mapping it to the shared ontology, generating a semantic
view, and making it queryable through the chat app. If you just want to
understand how the pipeline works internally, read `framework/README.md` and
`implementation-findings.md` first; this file is the practical "do this"
version.

## Two ways to onboard

| Method | Use when |
|---|---|
| **App UI** ("Onboard Source" tab) | You have access to the deployed Streamlit app and want a guided, visual run with no local setup. |
| **CLI** (`framework/onboard.py`) | You're scripting/automating onboarding, running it from CI, or want the full intermediate artifacts (`manifest.json`, `mapping.json`, generated YAML) saved to disk for review. |

Both run the exact same 4-phase pipeline (`extract_metadata.py` ->
`map_ontology.py` -> `instantiate_template.py` -> deploy + validate). Pick
whichever is more convenient.

---

## Prerequisites

1. **The source tables already exist in Snowflake**, in one schema, e.g.
   `MY_DB.RAW`. This pipeline maps and models existing tables -- it does not
   load data for you (see `scripts/load_to_snowflake.py` if you're loading
   fresh CSVs first).
2. **A role with**:
   - `USAGE` on the source database/schema and `SELECT` on its tables (needed
     to profile columns and sample rows for FK inference).
   - `CREATE SEMANTIC VIEW` on the target schema (where the generated view
     will be deployed).
   - `USAGE` on a warehouse (metadata profiling and validation queries run
     real SQL).
3. **A Cortex-enabled account** -- ontology mapping uses `AI_COMPLETE` for the
   3-pass LLM-assisted table/column matching.

---

## Step 1 -- Decide if your entities already exist in the ontology

Open `framework/ontology.json` and check whether your source's business
concepts already match an existing entity (`Supplier`, `Part`, `Facility`,
`Customer`, `Demand`, `DemandLine`, `Transaction`, `Inventory`, ...).

- **They match** (e.g. your new source is also "suppliers" or "shipments" in
  a different ERP) -- skip to Step 3. The mapping engine will match your
  columns to the existing entity via synonyms + LLM-assisted scoring.
- **They don't match** (a genuinely new business concept, e.g. "Returns" or
  "Warranty Claims") -- add a new entity first (Step 2).

## Step 2 -- (Only if needed) Add a new entity to the ontology

Add an entry to `framework/ontology.json`'s `"entities"` array:

```json
{
  "name": "Return",
  "aliases": ["RMA", "ReturnRequest"],
  "description": "A customer-initiated return of previously shipped goods.",
  "role": "return_event",
  "attributes": [
    {"name": "return_id", "type_category": "id", "required": true, "is_primary_key": true, "synonyms": ["rma_id", "return_no"]},
    {"name": "return_date", "type_category": "date", "required": true, "synonyms": ["rma_date"]},
    {"name": "return_qty", "type_category": "numeric", "required": true, "synonyms": ["qty_returned"]}
  ],
  "foreign_keys": [
    {"references_entity": "Transaction", "required": true}
  ]
}
```

**Rules that matter** (see `implementation-findings.md` for the incidents
that produced these):

- `foreign_keys` is the *only* source of truth for what relationships the
  generated view will contain -- an FK the extraction phase finds in the data
  but that isn't declared here will be ignored, not silently included.
- If the new entity has a **composite primary key** (e.g. keyed on two FK
  columns, like a snapshot/bridge table), that's fine -- the FK-inference
  engine handles composite PKs correctly (each member column is still a valid
  FK candidate).
- Every attribute needs a `type_category` (`id`, `numeric`, `date`, `text`,
  `other`) -- used for type-compatible FK matching.

Then add whatever new facts/metrics that entity needs to
`framework/metric_templates.json` (see the `landed_cost_per_unit` /
`days_of_inventory` entries there for a worked example of a multi-component
derived metric), and optionally a couple of questions to
`framework/persona_query_templates.json` so they get smoke-tested by the
validation gate.

## Step 3 -- Run the onboarding pipeline

### Option A: App UI

1. Open the deployed app, go to the **Onboard Source** tab.
2. Fill in:
   - **Source** database/schema (where your new tables live).
   - **Target** database/schema (where the semantic view should be created --
     usually the same `ANALYTICS` schema as everything else, so it stays part
     of the one canonical view's database, or a new schema if you want it
     kept separate).
   - **Semantic view name**.
3. Click **Run onboarding pipeline** and watch the status log run through the
   4 phases live.
4. Review the results (see Step 4 below) directly in the UI: metric values,
   dropped-relationship notices, verified-query pass/fail table, and
   expanders for the full mapping report and generated YAML.

### Option B: CLI

```bash
cd framework
python onboard.py --connection UU60334 \
    --source-db MY_DB --source-schema RAW \
    --target-db MY_DB --target-schema ANALYTICS \
    --view-name MY_ANALYTICS_VIEW
```

(Use `--connection UU60334_PAT` if you're running this as a standalone script
outside an already-authenticated session -- see Finding 4 in
`implementation-findings.md` for why PAT auth is used for CLI runs.)

Artifacts land in `framework/runs/<timestamp>/`:
- `manifest.json` -- extracted table/column profile + inferred FKs.
- `mapping.json` / `mapping_report.md` -- entity/attribute mapping + any
  cross-validation issues.
- `generated_view.yaml` -- the concrete semantic view YAML that got deployed.
- `validation_report.md` -- metric values, sanity-bound checks, verified
  query round-trip results.

## Step 4 -- Review the result

**If mapping was REJECTED** (`mapping_report.md` says
`overall_status: REJECTED_NEEDS_HUMAN_RESOLUTION`): a required entity
couldn't be mapped, or two relationships collided. Open the report, find the
`## Cross-Validation Issues` section, and either:
- Fix the ontology (e.g. the entity really doesn't exist in your ontology
  yet -- go back to Step 2), or
- Fix the source schema mapping by re-running with a hint, or accept a lower
  match and manually adjust `mapping.json` before re-running Phase 1c/1d
  (advanced; ask before doing this if unsure).

**If mapping succeeded but validation shows FAIL rows**: check whether it's a
genuinely wrong metric (investigate the generated YAML's expression) or a
known limitation already documented in `implementation-findings.md` (e.g. the
ambiguous-dimension-name issue, or a semantic-view `VARIABLES` incompatibility
with Cortex Analyst).

**If everything PASSes**: your new semantic view is live at
`<target_db>.<target_schema>.<view_name>`.

---

## Step 5 -- Use the new source

### Query it directly with SQL

```sql
SELECT * FROM SEMANTIC_VIEW(MY_DB.ANALYTICS.MY_ANALYTICS_VIEW
    METRICS <metric1>, <metric2>
    DIMENSIONS <table>.<dimension1>)
```

### Query it through the chat app

1. Open the deployed app's **Ask Questions** tab.
2. In the sidebar, the **Semantic view** dropdown lists every semantic view
   in the selected database -- pick your new one.
3. Ask questions in plain English. The app first tries the real Cortex Agent
   registered for the selected view in `SOURCE_AGENT_REGISTRY` (see below --
   onboarding creates and registers this automatically) and falls back to a
   prompt-engineered `AI_COMPLETE` path if that fails for any reason.

### The onboarding pipeline already creates a dedicated agent for you

Both onboarding methods (App UI and CLI) automatically deploy a single-tool
Cortex Agent bound to the new view as the final pipeline step -- you don't
need to hand-edit any agent YAML. See `framework/agent_template.py` for the
generator; `implementation-findings.md` Finding 11 for the full design
rationale (one agent per source, vs. one agent with multiple tools).

What happens automatically:
- A new agent `<target_db>.APP.<view_name>_AGENT` is created, with the same
  STRICT SCOPE BOUNDARY guardrail (Finding 9) and the same Jira MCP
  ticket-filing capability (Finding 10) as the canonical `SUPPLY_CHAIN_AGENT`
  -- just re-scoped to this source's one semantic view.
- `USAGE`/`SELECT` are granted to `SUPPLY_CHAIN_APP_ROLE` and the 3 persona
  roles on both the new agent and the new semantic view.
- The mapping `view_fqn -> agent_fqn` is written to
  `SC_DEMO.APP.SOURCE_AGENT_REGISTRY`, which `app/qa.py`'s `ask()` reads to
  decide which agent to call for whichever semantic view is selected in the
  chat app's sidebar dropdown -- so the dropdown selection now actually
  determines both the view **and** the agent, with no app code change needed
  for future sources.

If you need a single agent that can reason across *two* sources at once
(rare -- most questions are scoped to one source), that's a deliberate
manual step, not something onboarding does for you: add a second
`cortex_analyst_text_to_sql` tool to an existing agent's spec pointing at
the second view (see `app/qa.py`'s docstring and Finding 6 for the exact
YAML shape), being careful to write mutually-exclusive tool descriptions so
the orchestration model doesn't have to guess between them.

### Grant access to a persona / team

If a specific team (planning/procurement/logistics, or a new one) needs
access to the new view:

```sql
CREATE ROLE IF NOT EXISTS MY_TEAM_ROLE;
GRANT USAGE ON DATABASE MY_DB TO ROLE MY_TEAM_ROLE;
GRANT USAGE ON SCHEMA MY_DB.ANALYTICS TO ROLE MY_TEAM_ROLE;
GRANT SELECT ON SEMANTIC VIEW MY_DB.ANALYTICS.MY_ANALYTICS_VIEW TO ROLE MY_TEAM_ROLE;
-- if they should also use the Cortex Agent directly:
GRANT USAGE ON AGENT MY_DB.APP.MY_AGENT TO ROLE MY_TEAM_ROLE;
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE MY_TEAM_ROLE;
```

Grant the role to the app's own service role too if it should be selectable
from the deployed app's persona toggle (`GRANT ROLE MY_TEAM_ROLE TO ROLE
SUPPLY_CHAIN_APP_ROLE`), then add it to the `PERSONAS` dict in `app/app.py`.

---

## Troubleshooting quick reference

| Symptom | Likely cause | Where to look |
|---|---|---|
| New entity mapped with 0.00 confidence | Composite-PK FK-inference bug (fixed) or a genuinely missing/misnamed column | `mapping_report.md` rationale column |
| `Multi-path relationship ... is not supported` | Ontology declares (or the extraction found) two relationship paths between the same two tables | Finding 2 / Finding 3 in `implementation-findings.md` |
| A cross-table metric is silently wrong (but compiles) | Metric defined as table-scoped instead of a view-level derived metric | Finding 1 -- check `kind` in `metric_templates.json` |
| `invalid identifier` when the Cortex Agent (not direct SQL) runs a metric | A fact/metric references a top-level semantic-view `VARIABLE` | Finding 6 -- inline the variable's default value instead |
| Ambiguous dimension name error in a verified query | Same dimension name (e.g. `region`) exists on two tables, referenced unqualified | Fixed in `instantiate_template.py`'s `resolve_verified_queries()` -- qualify with the table alias |

For the full incident history, always check `implementation-findings.md`
before assuming something is a new bug.
