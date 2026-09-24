---
name: supply-chain-onboarding
description: "Onboard a new supply-chain source schema onto the governed semantic layer by automatically mapping it to the abstract supply-chain ontology and generating a validated Snowflake semantic view. Use when: adding a new source database/schema to the supply-chain analytics framework, mapping an arbitrary schema to the ontology, generating a semantic view without hand-authoring YAML, or re-running onboarding after a source schema changes. Triggers: onboard supply chain source, map schema to ontology, generate semantic view from schema, add new source to supply chain framework, run onboarding pipeline."
---

# Supply Chain Onboarding

Wraps `framework/onboard.py`, which chains four phases to turn an arbitrary
supply-chain-shaped source schema into a working, validated Snowflake semantic
view -- with zero hand-authored YAML:

1. **Extract** (`extract_metadata.py`) -- introspect columns/types/PKs/declared
   FKs; infer undeclared FKs via naming-convention + value-containment checks.
2. **Map** (`map_ontology.py`) -- 3-pass mapping of the source schema onto
   `framework/ontology.json` (structural scoring, LLM-assisted disambiguation
   via `AI_COMPLETE`, automated cross-validation).
3. **Instantiate** (`instantiate_template.py`) -- resolve `metric_templates.json`
   / `persona_query_templates.json` placeholders against the mapping and emit
   a complete semantic-view YAML.
4. **Deploy + validate** (inside `onboard.py`) -- create the target schema,
   deploy the view via `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML`, run sanity-bound
   checks and a verified-query round-trip.

See `reference.md` for the ontology/template file formats and the correctness
rules this engine already handles automatically -- read it before treating any
failure as a "new" bug.

## Prerequisites

- An active Snowflake connection (`cortex connections list`) with access to
  both the source schema and permission to create the target schema/view.
- `GRANT USE AI FUNCTION AI_COMPLETE ON ACCOUNT TO ROLE <role>` -- Phase 2's
  LLM-assisted disambiguation calls `SNOWFLAKE.CORTEX.AI_COMPLETE` and fails
  with `Unknown function AI_COMPLETE` without this grant, even for ACCOUNTADMIN.
  Check with `SHOW GRANTS TO ROLE <role>`; grant if missing.
- The source schema should resemble the supply-chain ontology (suppliers,
  parts, facilities, customers, demand/orders, order lines, transactions) --
  this framework maps *into* that fixed ontology, it does not invent a new one
  per source.

## Workflow

### Step 1: Gather inputs

Ask the user for (or confirm if already stated):
- `--connection` (defaults to the active connection)
- `--source-db` / `--source-schema`
- `--target-db` / `--target-schema` (created if it doesn't exist)
- `--view-name`

**STOP**: confirm these five values before running anything -- the pipeline
deploys a real object.

### Step 2: Run the pipeline

Standalone script invocations use a Personal Access Token (PAT), not browser
SSO, to avoid repeated login prompts (see `reference.md` for why). Use a
PAT-authenticated connection profile and inject the token via `cortex secret
run` -- not `bash`'s `secret_env`, which may not propagate in every environment:

```
cortex secret run --map "<your-pat-secret-key>=SNOWFLAKE_PAT" -- python framework/onboard.py --connection <conn>_PAT \
    --source-db <SRC_DB> --source-schema <SRC_SCHEMA> \
    --target-db <TGT_DB> --target-schema <TGT_SCHEMA> \
    --view-name <VIEW_NAME>
```

Run via the project's Python interpreter. Artifacts land in
`framework/runs/<timestamp>/`: `manifest.json`, `mapping.json`,
`mapping_report.md`, `generated_view.yaml`, `validation_report.md`.

### Step 3: Check the mapping report

Read `mapping_report.md` from the new run directory.

- If `overall_status != OK` (Pass 3 cross-validation found a blocking issue --
  e.g. a required ontology relationship with no FK path, or an unmapped
  required attribute), the script already stopped before deploying anything.
  **STOP**: present the specific issue to the user. Resolution is either
  extending `ontology.json`/the source schema, or accepting that this source
  doesn't fit the ontology as-is.
- If any entity is `requires_human_mapping` (Pass 1+2 confidence < 0.7),
  **STOP**: surface those entities/tables to the user for manual confirmation
  before proceeding, even though the script itself doesn't block on this.
- Entities marked `flagged_for_review` (confidence 0.7-0.9) are non-blocking --
  mention them, don't stop.

### Step 4: Check the validation report

Read `validation_report.md`. Present to the user:
- Metric values from the deployed view
- Sanity-bound check results (fill_rate range, lead-time range,
  perfect_order_rate <= otif_rate <= on_time_delivery_rate)
- Verified-query round-trip pass/fail per persona query

### Step 5: Report result

- If validation is `PASS`: report success with the deployed view FQN
  (`<target-db>.<target-schema>.<view-name>`) and a one-line metric summary.
- If `NEEDS REVIEW`: **STOP** and list exactly which checks/queries failed --
  do not describe the run as successful.

## Alternative: the deployed SPCS app

A Streamlit app (`app/`) deployed to Snowpark Container Services wraps this
same pipeline behind a UI, plus a chat interface for asking natural-language
questions against any deployed semantic view (via `AI_COMPLETE`, grounded in
`DESCRIBE SEMANTIC VIEW`). It runs with its own scoped role's Snowflake
session (`SUPPLY_CHAIN_APP_ROLE`) -- no PAT, no CLI. See the repo root
`README.md` for the deployed URL and how to rebuild/redeploy via the GitHub
Actions workflow in `.github/workflows/deploy.yml`. Use this skill (the CLI
path) for agent-driven onboarding in a CoCo session; use the app for a human
clicking through a browser.

## Known limitations

Mention these if relevant to the user's question, don't over-explain unless asked:

- Month-level time dimensions aren't auto-generated (one persona query,
  `otif_trend_by_month`, is skipped for this reason).
- Pass-1-only structural confidence caps at 0.80, so even a decisively correct
  match never reaches the >=0.9 auto-accept threshold and shows as
  `flagged_for_review`.
- Validated so far against one schema shape (`SC_DEMO.RAW`, run blind). Not yet
  proven against a genuinely different shape (renamed columns, denormalized
  tables, split entities).
- If two entities share a dimension attribute name (e.g. `Supplier.region` and
  `Facility.region`), a verified query mixing both is rejected as an
  "ambiguous semantic expression" -- not yet auto-resolved with entity-qualified
  names.
- This skill does not apply access-control grants on the deployed view --
  that's a separate, not-yet-implemented step.

## Stopping Points

- After Step 1: inputs confirmed
- After Step 3: if mapping is blocked (`overall_status != OK`) or has
  `requires_human_mapping` entities
- After Step 5: final pass/needs-review result

## Output

A deployed Snowflake semantic view at `<target-db>.<target-schema>.<view-name>`,
plus five artifacts in `framework/runs/<timestamp>/` documenting exactly how
the mapping and generated YAML were derived.
