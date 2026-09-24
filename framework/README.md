# Ontology Mapping / Template Instantiation Engine

Implements design doc Sections 1b/1c/1d (and Section 8 sequence items 5-6):
the generalized onboarding framework that maps an arbitrary source schema to
the abstract supply-chain ontology and instantiates a concrete, correct
Snowflake semantic view -- without hand-authoring per-source YAML.

## Pipeline

1. **`extract_metadata.py`** (Phase 1a) -- introspects a source schema
   (columns, types, PKs, declared FKs) and infers undeclared FKs via a
   naming-convention filter + value-containment check (many real schemas,
   including this project's own `SC_DEMO.RAW`, have no declared FK
   constraints at all).
2. **`map_ontology.py`** (Phase 1b) -- three-pass mapping against
   `ontology.json`: Pass 1 structural scoring (no LLM), Pass 2 LLM-assisted
   disambiguation via `SNOWFLAKE.CORTEX.AI_COMPLETE` (only called when Pass 1
   is ambiguous), Pass 3 automated cross-validation.
3. **`instantiate_template.py`** (Phase 1c) -- resolves `metric_templates.json`
   / `persona_query_templates.json` placeholders using the mapping and emits
   a complete semantic-view YAML. Encodes three correctness rules
   automatically (see `implementation-findings.md`):
   - cross-entity metrics become YAML top-level *derived metrics*, never
     table-scoped (avoids fan-out)
   - relationships are scoped to what the ontology explicitly declares, not
     every FK the extraction happens to find (avoids undeclared multi-path
     relationships)
   - a sampled-row SQL check detects denormalized FKs (a direct relationship
     that's just a copy of a value reachable transitively) and drops the
     redundant one
   - numeric attributes referenced cross-table from a row-level fact
     expression get an auto-generated "passthrough fact" so Snowflake can
     resolve the reference
4. **`onboard.py`** -- CLI orchestrator chaining all four phases, writing
   artifacts to `runs/<timestamp>/`.

## Files

- `ontology.json` -- abstract entity/attribute/relationship definitions.
- `metric_templates.json` -- the 8 canonical metrics as placeholder templates.
- `persona_query_templates.json` -- persona questions as placeholder templates.
- `sf_connect.py` -- shared PAT-aware Snowflake connection helper (see below).
- `runs/` -- output artifacts from actual pipeline runs (manifest, mapping,
  generated view, validation report) against `SC_DEMO.RAW`.

## Acceptance result

Running the full pipeline against `SC_DEMO.RAW` (targeting
`SC_DEMO.ANALYTICS_AUTO.SUPPLY_CHAIN_ANALYTICS_AUTO`), with **zero manual
mapping input**, reproduced every canonical metric to an exact match against
the hand-built ground-truth view (`SC_DEMO.ANALYTICS.SUPPLY_CHAIN_ANALYTICS`).
See `runs/validation_report.md` for the full comparison and a third
empirically-discovered correctness rule (undeclared multi-path relationships)
found only while building this automation.

## Usage

Standalone script invocations authenticate via a Personal Access Token (PAT)
instead of browser SSO -- see "Avoiding repeated browser SSO prompts" below.

```
cortex secret run --map "sf-scripts-pat=SNOWFLAKE_PAT" -- python onboard.py \
    --connection UU60334_PAT \
    --source-db SC_DEMO --source-schema RAW \
    --target-db SC_DEMO --target-schema ANALYTICS_AUTO \
    --view-name SUPPLY_CHAIN_ANALYTICS_AUTO
```

## Avoiding repeated browser SSO prompts

Standalone Python scripts (run outside `sql_execute`/`python_repl`'s
pre-authenticated session) can't rely on browser-SSO token caching: the
standalone interpreter has no working OS secure-storage backend, and even
with `keyring` installed, Windows Credential Manager rejects OAuth tokens
this large (`CredWrite` error 1783). The fix used here is a Personal Access
Token (PAT) instead -- PAT auth involves no browser interaction at all.

Setup (one-time, already done for this project):
1. A permissive network policy (`ALLOWED_IP_LIST=('0.0.0.0/0')`) is attached
   to the user -- required by Snowflake for a human user to *authenticate*
   with a PAT (generating one doesn't require it, using one does). This
   doesn't narrow real access since there was no network policy before.
2. `ALTER USER ... ADD PROGRAMMATIC ACCESS TOKEN framework_scripts_pat
   ROLE_RESTRICTION = 'ACCOUNTADMIN' DAYS_TO_EXPIRY = 365` generated the
   token; the secret is stored via `cortex secret store sf-scripts-pat`
   (never written to any file in plaintext).
3. `~/.snowflake/connections.toml` has a `[UU60334_PAT]` profile
   (`authenticator = "PROGRAMMATIC_ACCESS_TOKEN"`, `role = "ACCOUNTADMIN"`,
   no secret in the file) alongside the original `[UU60334]` OAuth profile
   used for interactive sessions.
4. `sf_connect.py` is a shared helper all 4 scripts use: when the
   `SNOWFLAKE_PAT` env var is set, it's passed to the connector as the
   `token` parameter (not `password` -- that's silently rejected as
   "invalid"), overriding the profile's own auth.

Run any script with the token injected via `cortex secret run` (never
`bash`'s `secret_env`, which doesn't propagate into this environment):
```
cortex secret run --map "sf-scripts-pat=SNOWFLAKE_PAT" -- python <script.py> --connection UU60334_PAT ...
```

