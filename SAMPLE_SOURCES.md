# Sample Data Source Schemas

Two alternate source schemas exist directly in Snowflake for testing/demoing
the onboarding pipeline against genuinely different raw structures (naming
conventions, key styles, denormalization patterns) -- distinct from the main
`SC_DEMO.RAW` dataset the canonical semantic view was built from. Both are
granted to `SUPPLY_CHAIN_APP_ROLE` and were validated end-to-end via the CLI
pipeline (13/13 verified queries + all sanity-bound checks PASS) before
hand-off. Onboard them from the app's **Onboard Source** tab, or via
`framework/onboard.py` -- see `HOWTO_ONBOARD_NEW_SOURCE.md`.

## `SC_DEMO.RAW_LEGACY_ERP` -- SAP/legacy-ERP style export

Abbreviated cryptic column names, numeric surrogate keys, one denormalized FK
(`GOODS_RECEIPT.PLNT_ID`, mirroring the same pattern already present in the
main dataset's `SHIPMENTS.PLANT_ID`).

| Table | Rows | Columns | Maps to ontology entity |
|---|---|---|---|
| `VENDOR_MASTER` | 30 | `VEND_ID`, `VEND_NM`, `VEND_CTRY`, `VEND_RGN`, `VEND_CLS`, `LEAD_TM_DY` | Supplier |
| `MATERIAL_MASTER` | 80 | `MATL_ID`, `MATL_DESC`, `MATL_GRP`, `STD_CST`, `WT_KG`, `CRIT_FLG`, `PRIM_VEND_ID` | Part |
| `PLANT_MASTER` | 6 | `PLNT_ID`, `PLNT_NM`, `PLNT_CTRY`, `PLNT_RGN`, `CAP_UNITS_DY` | Facility |
| `CUST_MASTER` | 40 | `CUST_ID`, `CUST_NM`, `CUST_SEG`, `CUST_RGN` | Customer |
| `PO_HEADER` | 500 | `PO_NO`, `CUST_ID`, `PLNT_ID`, `PO_DT`, `REQ_DLV_DT`, `PO_STAT` | Demand |
| `PO_ITEM` | 1,200 | `PO_ITM_ID`, `PO_NO`, `MATL_ID`, `ORD_QTY`, `UNIT_PR` | DemandLine |
| `GOODS_RECEIPT` | 1,500 | `GR_ID`, `PO_ITM_ID`, `VEND_ID`, `GR_DT`, `PROM_DT`, `SHP_QTY`, `RCV_QTY`, `DEFECT_QTY`, `FRT_AMT`, `DUTY_AMT`, `PLNT_ID` (denormalized copy from `PO_HEADER`, 0 mismatches) | Transaction |
| `STOCK_LEVELS` | 399 | `MATL_ID`, `PLNT_ID` (composite PK), `ON_HAND_QTY`, `SNAP_DT` | Inventory |

**PK/data types**: all numeric surrogate keys (`NUMBER`), plain `DATE` columns.

## `SC_DEMO.RAW_3PL` -- 3PL/logistics-provider style export

Text/UUID-style IDs, `TIMESTAMP_NTZ` instead of `DATE`, entirely different
entity naming (carrier/depot/account/SKU instead of supplier/plant/customer/
part), and a deliberately denormalized `DEPOT_ID` on `FULFILLMENTS` (copied
from the order's real depot via `ORDER_LINES -> ORDERS`, 0 mismatches -- a
true denormalization-detection test case, not independently random).

| Table | Rows | Columns | Maps to ontology entity |
|---|---|---|---|
| `CARRIERS` | 25 | `CARRIER_ID`, `CARRIER_NAME`, `CARRIER_COUNTRY`, `CARRIER_REGION`, `SERVICE_TIER`, `TRANSIT_DAYS_COMMITTED` | Supplier |
| `SKU_CATALOG` | 120 | `SKU_ID`, `SKU_DESC`, `SKU_CATEGORY`, `UNIT_COST`, `WEIGHT_KG`, `IS_HAZMAT`, `PRIMARY_CARRIER_ID` | Part |
| `DEPOTS` | 6 | `DEPOT_ID`, `DEPOT_NAME`, `DEPOT_COUNTRY`, `DEPOT_REGION`, `DAILY_CAPACITY_UNITS` | Facility |
| `ACCOUNTS` | 60 | `ACCOUNT_ID`, `ACCOUNT_NAME`, `ACCOUNT_TIER`, `ACCOUNT_REGION` | Customer |
| `ORDERS` | 700 | `ORDER_ID`, `ACCOUNT_ID`, `DEPOT_ID`, `ORDER_TS`, `REQUESTED_BY_TS`, `ORDER_STATUS` | Demand |
| `ORDER_LINES` | 1,800 | `ORDER_LINE_ID`, `ORDER_ID`, `SKU_ID`, `QTY_REQUESTED`, `UNIT_PRICE` | DemandLine |
| `FULFILLMENTS` | 2,200 | `FULFILLMENT_ID`, `ORDER_LINE_ID`, `CARRIER_ID`, `DEPOT_ID` (denormalized), `SHIP_TS`, `DELIVERY_TS`, `PROMISED_TS`, `QTY_SHIPPED`, `QTY_RECEIVED`, `DEFECT_QTY`, `FREIGHT_COST`, `DUTY_COST` | Transaction |
| `STOCK_SNAPSHOTS` | 598 | `SKU_ID`, `DEPOT_ID` (composite PK), `QTY_ON_HAND`, `SNAPSHOT_TS` | Inventory |

**PK/data types**: all text-based IDs (`TEXT`, e.g. `'CARR-014'`, `'ORD-00042'`), `TIMESTAMP_NTZ` date columns.

## What onboarding these is expected to demonstrate

- The mapping engine correctly matches wildly different column-naming
  conventions to the same ontology entities via its 3-pass LLM-assisted
  matching, not just exact/synonym string matching.
- The denormalization-detection logic correctly identifies and drops the
  redundant direct FK (`GOODS_RECEIPT.PLNT_ID`, `FULFILLMENTS.DEPOT_ID`) in
  favor of the transitive path, exactly as it does for the main dataset's
  `SHIPMENTS.PLANT_ID`.
- Composite-PK snapshot tables (`STOCK_LEVELS`, `STOCK_SNAPSHOTS`) map
  correctly to the `Inventory` entity.
- Text/UUID-style keys and `TIMESTAMP_NTZ` columns are handled the same as
  numeric keys and `DATE` columns.

Building these surfaced two real, previously-hidden bugs in the mapping/
template engine (masked until now by a naming coincidence in the original
dataset where physical column names happened to equal the ontology's logical
attribute names) -- both are fixed; see `implementation-findings.md` and the
commit that added this file for details.

## `SC_DEMO.RAW_DOCS` -- unstructured supplier quality inspection reports

A third sample source, structurally unlike the two above: not a relational
table export at all, but 10 free-text incoming-quality-inspection reports
(`.txt`, ~1.3KB each, written like a real inspector's report -- report
number, findings paragraph, root cause, disposition, corrective action,
follow-up note) staged at `@SC_DEMO.RAW_DOCS.INSPECTION_REPORTS` and turned
into a structured table via `AI_EXTRACT`, not the ontology onboarding
pipeline (that pipeline maps existing relational tables; this demonstrates
the separate "document intelligence" path: unstructured text -> AI_EXTRACT
-> structured table -> added to the live semantic view).

Each report was written against a real, pre-existing defective shipment in
`SC_DEMO.RAW.SHIPMENTS` (same `shipment_id`), so the extracted data is a
genuine link back into the ontology, not a standalone table.

| Step | What happened |
|---|---|
| 1. Source documents | 10 `.txt` inspection reports, staged at `SC_DEMO.RAW_DOCS.INSPECTION_REPORTS` |
| 2. Extraction | `AI_EXTRACT` over each file with an 11-field schema (report_no, inspection_date, shipment_reference, supplier_name, part_name, quantity_received, quantity_rejected, defect_category, root_cause, disposition, is_repeat_issue) |
| 3. Structured landing table | `SC_DEMO.RAW_DOCS.QUALITY_INSPECTIONS` (typed, `report_no` PK, `shipment_id` FK to `SC_DEMO.RAW.SHIPMENTS`) |
| 4. Added to the live semantic view | `SC_DEMO.ANALYTICS.SUPPLY_CHAIN_ANALYTICS` now includes `quality_inspections` as a 9th logical table, joined via `QUALITY_INSPECTIONS_TO_SHIPMENTS`, with 3 new metrics (`inspection_count`, `total_quantity_rejected`, `repeat_issue_rate`) and 3 new dimensions (`defect_category`, `is_repeat_issue`, `inspection_date`) |
| 5. Verified | Direct `SEMANTIC_VIEW()` queries, a cross-table join to `supplier_name` (proving the new relationship resolves correctly through the existing relationship graph), and a real Cortex Agent question ("Which supplier has the most repeat quality inspection issues...") all return the correct answer: **MexicoSupply-017**, 8 reports, 75% repeat-issue rate, 911 units rejected -- matching the narrative deliberately written into the source documents (an escalating tool-wear/process-control pattern from one supplier). |
| 6. Governance re-checked | Persona roles (e.g. `SC_PLANNING_ROLE`) can query the new metrics through the governed view but have zero grants on `SC_DEMO.RAW_DOCS.QUALITY_INSPECTIONS` directly -- confirmed with `USE SECONDARY ROLES NONE` to rule out the account owner's own session-level secondary-role privileges masking the check (see `implementation-findings.md` Finding 8 for why that check needs care). |

**Gotcha found while doing this:** `CREATE OR REPLACE SEMANTIC VIEW` resets
grants on the view -- the persona roles' `SELECT` grants had to be reissued
after redeploying with the new table. Documented as Finding 8.
