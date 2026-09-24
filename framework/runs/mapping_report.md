# Ontology Mapping Report

Source: `SC_DEMO.RAW`  |  Ontology: `supply_chain_ontology_v1`
Overall status: **OK**

## Entity Mappings

| Entity | Table | Confidence | Action | Pass | Rationale |
|---|---|---|---|---|---|
| Supplier | SUPPLIERS | 0.80 | flagged_for_review | 1 | Pass 1 structural match decisive (score=0.80 vs runner-up 0.50) |
| Facility | PLANTS | 0.80 | flagged_for_review | 1 | Pass 1 structural match decisive (score=0.80 vs runner-up 0.50) |
| Customer | CUSTOMERS | 0.80 | flagged_for_review | 1 | Pass 1 structural match decisive (score=0.80 vs runner-up 0.50) |
| Demand | PURCHASE_ORDERS | 0.80 | flagged_for_review | 1 | Pass 1 structural match decisive (score=0.80 vs runner-up 0.50) |
| Transaction | SHIPMENTS | 0.80 | flagged_for_review | 1 | Pass 1 structural match decisive (score=0.80 vs runner-up 0.10) |
| Part | PARTS | 1.00 | auto_accept | 2 | The PARTS table has exact matches for all five expected attributes of the Part entity. |
| DemandLine | PO_LINES | 0.98 | auto_accept | 2 | PO_LINES perfectly matches DemandLine with exact column names for all three expected attributes. |

## Attribute Mappings

**Supplier** (`SUPPLIERS`)
  - `supplier_id` -> `SUPPLIER_ID`
  - `supplier_name` -> `SUPPLIER_NAME`
  - `country` -> `COUNTRY`
  - `region` -> `REGION`
  - `tier` -> `TIER`

**Facility** (`PLANTS`)
  - `facility_id` -> `PLANT_ID`
  - `facility_name` -> `PLANT_NAME`
  - `country` -> `COUNTRY`
  - `region` -> `REGION`

**Customer** (`CUSTOMERS`)
  - `customer_id` -> `CUSTOMER_ID`
  - `customer_name` -> `CUSTOMER_NAME`
  - `segment` -> `SEGMENT`
  - `region` -> `REGION`

**Demand** (`PURCHASE_ORDERS`)
  - `demand_id` -> `PO_ID`
  - `order_date` -> `ORDER_DATE`
  - `requested_delivery_date` -> `REQUESTED_DELIVERY_DATE`
  - `status` -> `PO_STATUS`

**Transaction** (`SHIPMENTS`)
  - `transaction_id` -> `SHIPMENT_ID`
  - `ship_date` -> `SHIP_DATE`
  - `delivery_date` -> `DELIVERY_DATE`
  - `promised_delivery_date` -> `PROMISED_DELIVERY_DATE`
  - `shipped_qty` -> `SHIPPED_QTY`
  - `received_qty` -> `RECEIVED_QTY`
  - `defect_qty` -> `DEFECT_QTY`

**Part** (`PARTS`)
  - `part_id` -> `PART_ID`
  - `part_name` -> `PART_NAME`
  - `category` -> `CATEGORY`
  - `unit_cost` -> `UNIT_COST`
  - `is_critical` -> `IS_CRITICAL`

**DemandLine** (`PO_LINES`)
  - `demand_line_id` -> `PO_LINE_ID`
  - `ordered_qty` -> `ORDERED_QTY`
  - `unit_price` -> `UNIT_PRICE`

## Cross-Validation Issues (Pass 3)

None -- mapping is internally consistent.