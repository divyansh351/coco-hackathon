# Persona Consistency Check

Demonstrates the original requirement: "the same metric resolves identically
across personas (planning, procurement, logistics)".

## Setup

Three independent RBAC roles were created, each granted `USAGE` on
`SC_DEMO`/`SC_DEMO.ANALYTICS`, `SELECT` on the canonical semantic view
`SC_DEMO.ANALYTICS.SUPPLY_CHAIN_ANALYTICS`, and `USAGE` on `COMPUTE_WH`:

- `SC_PLANNING_ROLE`
- `SC_PROCUREMENT_ROLE`
- `SC_LOGISTICS_ROLE`

No role received any other privilege on the underlying `SC_DEMO.RAW` tables --
each can only reach the data through the governed semantic view, so if the
metric definitions were duplicated or drifted per-team, this check would catch
it.

## Query run identically under all three roles

```sql
SELECT * FROM SEMANTIC_VIEW(SC_DEMO.ANALYTICS.SUPPLY_CHAIN_ANALYTICS
    METRICS otif_rate, fill_rate, days_of_inventory, landed_cost_per_unit)
```

## Result

| Role | OTIF_RATE | FILL_RATE | DAYS_OF_INVENTORY | LANDED_COST_PER_UNIT |
|---|---|---|---|---|
| SC_PLANNING_ROLE | 5.101200 | 81.87859900 | 30.16050651 | 461.2102648427 |
| SC_PROCUREMENT_ROLE | 5.101200 | 81.87859900 | 30.16050651 | 461.2102648427 |
| SC_LOGISTICS_ROLE | 5.101200 | 81.87859900 | 30.16050651 | 461.2102648427 |

**Byte-identical across all three roles**, for both original canonical metrics
(OTIF, fill rate) and the two newly added ones (days of inventory, landed cost
per unit).

## Honest scope note

This proves the governed semantic view itself is RBAC-independent and single-
source-of-truth: any team querying it -- regardless of role -- gets the same
number for the same metric, because the definition lives in one place (the
semantic view), not duplicated per-team logic. It does **not** demonstrate the
deployed Streamlit app switching personas at the UI level, since that app's
SPCS container runs under one fixed service role
(`SUPPLY_CHAIN_APP_ROLE`). A full per-persona UI experience would require
either separate per-persona service deployments or session-level role
switching inside the app, which is out of scope here. The roles created in
this check are real, independently usable Snowflake RBAC objects -- any of the
three teams could connect with their own client using the appropriate role and
get these same results directly.
