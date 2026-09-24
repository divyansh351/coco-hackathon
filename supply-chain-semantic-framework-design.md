# Governed Conversational Analytics Framework for Supply Chain Data

## Design Document — Snowflake Semantic Views

---

## 1. FRAMEWORK PRINCIPLES — ONBOARDING FLOW

The framework's core claim is that a natural-language question about supply chain performance should resolve to one consistent, explainable answer regardless of who asks it, how it's phrased, or which source system's schema sits underneath. To deliver that, onboarding a new source system must produce a semantic view whose metric definitions are locked to canonical formulas, not improvised per source.

The onboarding flow has four phases. For each, we note what is reusable framework logic (ships once, works for any source) versus what is necessarily instantiation-specific (produced fresh per source).

### Phase 1a: Metadata Extraction

**Automatically extractable via schema introspection:**

| Signal | Method | Confidence |
|---|---|---|
| Table names, column names, data types | `INFORMATION_SCHEMA.COLUMNS`, `SHOW COLUMNS` | High — mechanical |
| Foreign key relationships | `SHOW IMPORTED KEYS`, `SHOW PRIMARY KEYS` | High where declared; many ERP exports omit FKs |
| Row counts, null rates, cardinality per column | `APPROX_COUNT_DISTINCT`, `COUNT(*)`, null ratio | High — statistical |
| Date range spans per date/timestamp column | `MIN/MAX` queries | High |
| Sample distinct values (top-N by frequency) | `APPROX_TOP_K` or grouped count | High |

**Requires sample-data profiling or human confirmation:**

| Signal | Why introspection alone fails | Proposed method |
|---|---|---|
| Semantic role of date columns (promise vs. actual vs. receipt date) | Columns like `DT1`, `SHIP_DT` are ambiguous | LLM inference from name + sample values + co-occurring columns — **must** be human-confirmed; a wrong date role silently corrupts lead-time and on-time metrics |
| Whether a flag is a soft-delete | A `STATUS` column with `A`/`I` could mean active/inactive or approval state | Sample-value inspection + LLM heuristic; human-confirmed |
| Unit of measure for quantities | `QTY` could be eaches, cases, pallets | Requires human confirmation or a UOM reference table |
| Currency of monetary columns | Multi-currency sources may lack a currency column | Human confirmation required |
| Current-state vs. SCD2 history | `VALID_FROM`/`VALID_TO` presence is suggestive, not conclusive | Structural heuristic + human confirmation |
| Grain of a fact table | Critical for metric denominators | Candidate-key analysis (minimal unique column set) + human confirmation |

**Framework vs. instantiation:** Framework owns the profiling queries, LLM prompt templates, confidence rubric, and output manifest schema. Instantiation owns the actual extracted values and human confirmations.

### Phase 1b: Ontology Mapping (hardest, most important phase)

The framework defines an abstract ontology — canonical entity types (Supplier, Part, Facility, Shipment, Order, Customer) with expected attributes and relationship patterns. Matching happens in three passes, each adding confidence:

**Pass 1 — Structural signal matching (no LLM):** Match tables to entity types by FK graph position (a table referenced by a shipment-like table and referencing a customer-like table is probably an Order). Match columns to attribute slots by data type + cardinality (a VARCHAR near-unique-per-row in a supplier candidate is likely the name). Confidence typically 0.4–0.6 — enough to rank, not commit.

**Pass 2 — Semantic name matching (LLM-assisted):** For unmatched tables/columns, prompt an LLM with the source name, sample values, the ontology's entity/attribute descriptions, and Pass 1's structural context. Returns ranked candidates with explanations. Combined confidence typically 0.7–0.85.

**Pass 3 — Cross-validation (automated):** Check the proposed mapping is internally consistent — every ontology relationship has a corresponding FK path, no table maps to two entity types, every required attribute is mapped. Flag inconsistencies for human resolution.

**Confidence scoring and routing:**

| Score | Action |
|---|---|
| ≥ 0.9 | Auto-accept (safe for well-named ERP schemas like SAP `VBAK`/`VBAP`) |
| 0.7–0.9 | Present to human with LLM's explanation; confirm or override |
| < 0.7 | Require human mapping; show top candidates as suggestions only |

**Where human-in-the-loop is non-negotiable:** entity type assignment for ambiguous tables, semantic role of date columns, and — critically — any mapping feeding a metric calculation, since a wrong mapping there silently produces wrong KPIs. **Safe to trust automated:** primary key identification, obvious exact/near-exact name matches, data type matches.

**A genuinely hard sub-problem:** real schemas rarely map 1:1 to the ontology. A source might combine order and shipment in one denormalized table (1 source → N ontology entities, handled via Snowflake's role-playing tables — same `base_table`, different logical names), or split "Supplier" across a vendor master and vendor-site table (N source → 1 ontology entity, handled by making the logical table's `base_table` a SQL query/join instead of a bare table reference).

**Framework vs. instantiation:** Framework owns the ontology definition, the three-pass algorithm, confidence rubric, and LLM prompts. Instantiation owns the actual mappings, overrides, and composite table definitions.

### Phase 1c: Template Instantiation

Canonical metrics are stored as templates referencing ontology placeholders, not physical names:

```yaml
# Framework template (internal representation, not valid Snowflake YAML)
metric:
  name: fill_rate
  expr: "SUM({{Shipment.shipped_qty}}) / NULLIF(SUM({{OrderLine.ordered_qty}}), 0)"
  tables_required: [Shipment, OrderLine]
  relationship_path: [OrderLine -> Shipment]
```

Once Phase 1b resolves `Shipment` → logical table `shipments` with `shipped_qty` → `SHP_QTY`, and `OrderLine` → `order_lines` with `ordered_qty` → `ORD_QTY`, the engine emits the concrete Snowflake semantic-view metric:

```yaml
metrics:
  - name: fill_rate
    expr: "SUM(order_lines.shp_qty) / NULLIF(SUM(order_lines.ord_qty), 0)"
```

The engine also generates verified queries (instantiating the persona question templates from Section 7 with resolved logical names) and source-specific custom instructions (e.g., "quantities are in cases, not eaches").

**Framework vs. instantiation:** Framework owns the metric templates, template engine, and verified-query templates. Instantiation owns the generated YAML and any source-specific overrides.

### Phase 1d: Validation Gate

Before a newly instantiated view is trusted, it must pass a validation suite:

| Test | Check | Failure action |
|---|---|---|
| "What is the overall fill rate?" | 0%–100% | Reject — likely wrong column mapping |
| "What is the average lead time?" | Positive, < 365 days | Reject — likely wrong date columns |
| "Fill rate by supplier" | Multiple rows, all in bounds | Reject if single row or out of bounds |
| Cross-metric consistency | `fill_rate * on_time_rate ≈ OTIF` within tolerance | Flag for review if off by > 5pp |
| Verified query round-trip | Every VQ executes and returns non-empty results | Reject if any fails |

On failure, the view is **not** promoted — it stays in staging with a report of failed tests, actual values vs. bounds, and a return to Phase 1b with that context. **Why this can't be skipped:** the entire value proposition is "ask a question, get the right answer, every time." A confidently wrong answer attributed to a "canonical" definition is worse than no semantic layer at all — it launders bad data with false authority.

---

## 2. PROPOSED SCOPE FOR THE DEMONSTRATION INSTANTIATION

**Recommendation: 6 entities, 8 canonical metrics, 3 personas.**

Entities: Supplier, Part, Plant, PurchaseOrder (+ line items), Shipment, Customer. Metrics: Fill Rate, On-Time Delivery Rate, OTIF, Average Lead Time, Order Cycle Time, Supplier Defect Rate, Inventory Turnover (proxy), Perfect Order Rate. Personas: Supply Chain Manager, Procurement Analyst, Plant Operations Lead.

**Justification:** Six entities is the smallest set that forces a genuine modeling challenge — Shipment sits between Order and Supplier, creating a fan-out (partial fills, multiple shipments per line) that a trivial star schema wouldn't exercise. Eight metrics span the three major supply chain domains (delivery, quality, efficiency) and include at least two with genuinely debatable definitions (fill rate, OTIF — see Section 4), which is where the governance story actually bites. Three personas is the minimum that proves cross-persona consistency: SCM and Procurement both ask about supplier performance from different angles; SCM and Plant Ops both ask about lead time at different scopes. A fourth persona would add realism, not proof, while roughly doubling the verified-query surface. A smaller scope (4 entities/2 personas) doesn't create enough relationship complexity to stress the mapping engine; a larger one (10+ entities, inventory/forecast data) adds proof-of-scale but not proof-of-concept, and can be layered on later.

---

## 3. ENTITY-RELATIONSHIP MODEL

```
                    ┌────────────┐
                    │  SUPPLIER  │
                    │(supplier_id│
                    │    PK)     │
                    └─────┬──────┘
                          │ 1
                          │ M
               ┌──────────┴──────────┐
               │                     │
         ┌─────┴──────┐       ┌──────┴───────┐
         │   PART     │       │  SHIPMENT    │
         │ (part_id   │       │ (shipment_id │
         │   PK)      │       │   PK)        │
         └─────┬──────┘       └──┬───────┬───┘
               │ M               │ M     │ M
               │ 1               │ 1     │ 1
         ┌─────┴──────┐   ┌─────┴────┐  │
         │  PLANT     │   │ PURCHASE │  │
         │ (plant_id  │   │  ORDER   │  │
         │   PK)      │   │(po_id PK)│  │
         └────────────┘   │ LINE     │  │
                          └─────┬────┘  │
                                │ M     │
                                │ 1     │ 1
                          ┌─────┴───────┴──┐
                          │   CUSTOMER     │
                          │ (via PO header)│
                          └────────────────┘
```

| Entity | Table | PK | Key Attributes |
|---|---|---|---|
| Supplier | `suppliers` | `supplier_id` | `supplier_name`, `country`, `region`, `tier`, `lead_time_days_contracted` |
| Part | `parts` | `part_id` | `part_name`, `category`, `unit_cost`, `weight_kg`, `is_critical` |
| Plant | `plants` | `plant_id` | `plant_name`, `country`, `region`, `capacity_units_per_day` |
| Customer | `customers` | `customer_id` | `customer_name`, `segment`, `region` |
| PurchaseOrder | `purchase_orders` | `po_id` | `customer_id` FK, `plant_id` FK, `order_date`, `requested_delivery_date`, `po_status` |
| PurchaseOrderLine | `po_lines` | `po_line_id` | `po_id` FK, `part_id` FK, `ordered_qty`, `unit_price` |
| Shipment | `shipments` | `shipment_id` | `po_line_id` FK, `supplier_id` FK, `plant_id` FK, `ship_date`, `delivery_date`, `promised_delivery_date`, `shipped_qty`, `received_qty`, `defect_qty` |

**Relationships:** `po_to_customer` (M:1), `po_to_plant` (M:1), `po_line_to_po` (M:1), `po_line_to_part` (M:1), `shipment_to_po_line` (M:1 — multiple shipments can partially fulfill one line), `shipment_to_supplier` (M:1), `shipment_to_plant` (M:1).

**Metric ownership:** Shipment owns fill rate, on-time rate, OTIF, defect rate, perfect order rate, lead time (it's the transactional fact table). PurchaseOrder owns order cycle time (order-level aggregation of shipment dates).

**Generalized pattern vs. instantiation-specific:** The generalized pattern is the *role* structure — Consumer → Demand → Item ← Source, with Facility as a location node touching both Demand and Transaction, and Transaction fanning out from a Demand line to multiple fulfillments. What's instantiation-specific: the `tier`/`is_critical` attributes (another org might use a rating scale instead), the header/line split (some sources denormalize this), and where `promised_delivery_date` lives (shipment vs. order header — a real modeling choice, discussed next).

---

## 4. CANONICAL METRIC DEFINITIONS

**Fill Rate**
```
fill_rate = SUM(shipments.shipped_qty) / NULLIF(SUM(po_lines.ordered_qty), 0) * 100
```
*Debatable:* ratio-of-quantity (chosen) vs. binary per-line-complete. A line that ordered 100 and got 99 scores 99% under ratio but 0% under binary. Ratio is more informative for partial-fill manufacturing scenarios; binary is more common in retail/distribution. Framework should expose both as a variant.

**On-Time Delivery Rate**
```
on_time_rate = AVG(CASE WHEN delivery_date <= promised_delivery_date THEN 1.0 ELSE 0.0 END) * 100
```
*Debatable:* strict `<=` (chosen) vs. a tolerance window, and promise against *original* vs. *current/revised* date.

**OTIF**
```
otif = AVG(CASE WHEN delivery_date <= promised_delivery_date AND shipped_qty >= po_lines.ordered_qty THEN 1.0 ELSE 0.0 END) * 100
```
*Debatable:* grain — per-shipment (chosen, simplest), per-order-line (all shipments for a line must collectively qualify), or per-order (strictest, common in FMCG/retail).

**Average Lead Time (days)**
```
avg_lead_time = AVG(DATEDIFF('day', purchase_orders.order_date, shipments.delivery_date))
```
Measures procurement lead time specifically; the date pair used should be parameterized since "lead time" is overloaded (procurement vs. manufacturing vs. total).

**Order Cycle Time (days)** — `AVG(DATEDIFF('day', order_date, MAX(delivery_date)))` per order; the customer-facing "how long until fully fulfilled" metric.

**Supplier Defect Rate**
```
supplier_defect_rate = SUM(defect_qty) / NULLIF(SUM(received_qty), 0) * 100
```
*Debatable:* unit-level (chosen, more precise for continuous improvement) vs. shipment-level binary (any defect = defective shipment, simpler for scorecards).

**Inventory Turnover** — genuinely **not fully computable** from this transactional model without inventory snapshot data. This is worth surfacing rather than papering over: flagged as requiring supplementary data, shipping a labeled proxy (`COGS / annualized average order value`) rather than pretending it's the real metric.

**Perfect Order Rate** — conjunction of on-time + in-full + defect-free per shipment; some definitions add documentation accuracy as a fourth leg, omitted here since the data model doesn't track it.

**Template re-expression pattern:**
```yaml
metric_templates:
  - name: fill_rate
    ontology_refs:
      - {entity: Transaction, attribute: shipped_qty}
      - {entity: DemandLine, attribute: ordered_qty}
    expr_template: "SUM({Transaction.shipped_qty}) / NULLIF(SUM({DemandLine.ordered_qty}), 0) * 100"
    sanity_bounds: [0, 100]
```
The entity/attribute references are the portable part; resolved logical column names are instantiation-specific.

---

## 5. SEMANTIC VIEW / ONTOLOGY STRUCTURE

**Architecture: single semantic view** (`supply_chain_analytics`). Snowflake's Cortex Agents **cannot join across separate semantic views**, so all six logical tables belong in one view for the demo — within Snowflake's recommended scope (<10 tables, well under the ~100K token guideline). At production scale with more entities, split by business domain (Procurement Performance, Customer Fulfillment, Quality) with non-overlapping custom instructions so Cortex Agents routes correctly instead of attempting cross-view joins.

Key structural choices, grounded in the actual Snowflake YAML spec:

- **Logical tables** map 1:1 to the physical tables in Section 3, each with `primary_key`, `dimensions`, `time_dimensions`, and `facts`.
- **Helper facts marked `access_modifier: private_access`** (`is_on_time`, `is_in_full`, `is_defect_free`, `lead_time_days`) — visible only as building blocks for metrics, not exposed directly, so users can't misuse the raw flag independent of the governed metric.
- **Metrics** (`fill_rate`, `on_time_delivery_rate`, `otif_rate`, `avg_lead_time_days`, `supplier_defect_rate`, `perfect_order_rate`) live on the `shipments` logical table since that's the grain they aggregate from.
- **Relationships** are declared explicitly (Cortex Agents does not infer joins that aren't declared) matching the FK list in Section 3.
- **Variables** (`on_time_tolerance_days`, `fill_rate_method`) parameterize exactly the two debatable choices flagged in Section 4, resolved at query time via `SEMANTIC_VIEW(... VARIABLES on_time_tolerance_days = 2 ...)` — this is how the framework's "generalized/parameterized" story maps onto a real Snowflake construct rather than being baked into fixed `expr` logic.
- **`module_custom_instructions.sql_generation`** pins down non-negotiable behavior (percentages aren't already-percentages don't get re-multiplied by 100; "supplier performance" defaults to a specific metric trio; "lead time" unqualified means `avg_lead_time_days`) — this is where governance is enforced at the SQL-generation layer, not just in metric formulas.
- **`module_custom_instructions.question_categorization`** redirects out-of-scope questions (e.g., inventory-on-hand) rather than letting the model guess.
- **Verified queries** embedded under `verified_queries:`, using the persona question set from Section 7, referencing logical names as required by Snowflake's VQR spec.

**Preventing cross-view inconsistency:** structurally moot for the single-view demo. For a future multi-view production architecture: (1) every view that exposes a shared metric name uses the same template output, so the formula is byte-identical across views; (2) custom instructions explicitly scope each view's coverage so Cortex Agents doesn't attempt a cross-view join it can't do; (3) any cross-table metric is defined once as a Snowflake **derived metric** at the view level rather than duplicated per logical table.

### Full Semantic View YAML (demonstration instantiation)

```yaml
name: supply_chain_analytics
description: >
  Governed supply chain analytics covering procurement, delivery, and quality
  metrics across suppliers, parts, plants, orders, and customers. Use this
  view to analyze fill rates, on-time delivery, OTIF, lead times, defect
  rates, and perfect order rates. Supports drill-down by supplier tier,
  part criticality, plant region, and time period.

tables:
  - name: suppliers
    base_table:
      database: SC_DEMO
      schema: RAW
      table: SUPPLIERS
    primary_key:
      columns: [supplier_id]
    dimensions:
      - name: supplier_name
        expr: supplier_name
        data_type: VARCHAR
        description: "Legal name of the supplier"
      - name: supplier_country
        expr: country
        data_type: VARCHAR
        description: "Country where the supplier is headquartered"
      - name: supplier_region
        expr: region
        data_type: VARCHAR
        description: "Geographic region (APAC, EMEA, AMER)"
        is_enum: true
      - name: supplier_tier
        expr: tier
        data_type: VARCHAR
        description: "Strategic classification: Strategic, Preferred, or Approved"
        is_enum: true
    facts:
      - name: contracted_lead_time
        expr: lead_time_days_contracted
        data_type: NUMBER
        description: "Contractually agreed lead time in days"

  - name: parts
    base_table:
      database: SC_DEMO
      schema: RAW
      table: PARTS
    primary_key:
      columns: [part_id]
    dimensions:
      - name: part_name
        expr: part_name
        data_type: VARCHAR
      - name: part_category
        expr: category
        data_type: VARCHAR
        is_enum: true
      - name: is_critical_part
        expr: is_critical
        data_type: BOOLEAN
        description: "Whether this part is classified as supply-critical"
        labels:
          - filter
    facts:
      - name: unit_cost
        expr: unit_cost
        data_type: NUMBER
      - name: weight_kg
        expr: weight_kg
        data_type: NUMBER

  - name: plants
    base_table:
      database: SC_DEMO
      schema: RAW
      table: PLANTS
    primary_key:
      columns: [plant_id]
    dimensions:
      - name: plant_name
        expr: plant_name
        data_type: VARCHAR
      - name: plant_country
        expr: country
        data_type: VARCHAR
      - name: plant_region
        expr: region
        data_type: VARCHAR
        is_enum: true

  - name: customers
    base_table:
      database: SC_DEMO
      schema: RAW
      table: CUSTOMERS
    primary_key:
      columns: [customer_id]
    dimensions:
      - name: customer_name
        expr: customer_name
        data_type: VARCHAR
      - name: customer_segment
        expr: segment
        data_type: VARCHAR
        description: "Customer segment: Enterprise, Mid-Market, or SMB"
        is_enum: true
      - name: customer_region
        expr: region
        data_type: VARCHAR
        is_enum: true

  - name: purchase_orders
    base_table:
      database: SC_DEMO
      schema: RAW
      table: PURCHASE_ORDERS
    primary_key:
      columns: [po_id]
    dimensions:
      - name: po_status
        expr: po_status
        data_type: VARCHAR
        is_enum: true
        description: "Order status: Open, Shipped, Delivered, Closed"
    time_dimensions:
      - name: order_date
        expr: order_date
        data_type: DATE
        description: "Date the purchase order was placed"
      - name: requested_delivery_date
        expr: requested_delivery_date
        data_type: DATE
        description: "Customer's requested delivery date"

  - name: po_lines
    base_table:
      database: SC_DEMO
      schema: RAW
      table: PO_LINES
    primary_key:
      columns: [po_line_id]
    facts:
      - name: ordered_qty
        expr: ordered_qty
        data_type: NUMBER
        description: "Quantity ordered by the customer on this line"
      - name: unit_price
        expr: unit_price
        data_type: NUMBER
      - name: line_value
        expr: ordered_qty * unit_price
        data_type: NUMBER
        access_modifier: private_access
        description: "Line-level order value (helper for metrics)"

  - name: shipments
    base_table:
      database: SC_DEMO
      schema: RAW
      table: SHIPMENTS
    primary_key:
      columns: [shipment_id]
    time_dimensions:
      - name: ship_date
        expr: ship_date
        data_type: DATE
      - name: delivery_date
        expr: delivery_date
        data_type: DATE
        description: "Actual date goods were received at the plant"
      - name: promised_delivery_date
        expr: promised_delivery_date
        data_type: DATE
        description: "Date the supplier promised delivery"
    facts:
      - name: shipped_qty
        expr: shipped_qty
        data_type: NUMBER
      - name: received_qty
        expr: received_qty
        data_type: NUMBER
      - name: defect_qty
        expr: defect_qty
        data_type: NUMBER
      - name: is_on_time
        expr: "CASE WHEN delivery_date <= promised_delivery_date THEN 1.0 ELSE 0.0 END"
        data_type: NUMBER
        access_modifier: private_access
        description: "Binary flag: 1 if delivered on or before promise"
      - name: is_in_full
        expr: "CASE WHEN shipped_qty >= po_lines.ordered_qty THEN 1.0 ELSE 0.0 END"
        data_type: NUMBER
        access_modifier: private_access
      - name: is_defect_free
        expr: "CASE WHEN defect_qty = 0 THEN 1.0 ELSE 0.0 END"
        data_type: NUMBER
        access_modifier: private_access
      - name: lead_time_days
        expr: "DATEDIFF('day', purchase_orders.order_date, delivery_date)"
        data_type: NUMBER
        access_modifier: private_access
    metrics:
      - name: fill_rate
        expr: "SUM(shipped_qty) / NULLIF(SUM(po_lines.ordered_qty), 0) * 100"
        description: "Ratio of shipped qty to ordered qty, as a percentage"
      - name: on_time_delivery_rate
        expr: "AVG(is_on_time) * 100"
        description: "Percentage of shipments delivered on or before the promised date"
      - name: otif_rate
        expr: "AVG(CASE WHEN is_on_time = 1.0 AND is_in_full = 1.0 THEN 1.0 ELSE 0.0 END) * 100"
        description: "Percentage of shipments that are both on-time and in-full"
      - name: avg_lead_time_days
        expr: "AVG(lead_time_days)"
        description: "Average days from order placement to delivery"
      - name: supplier_defect_rate
        expr: "SUM(defect_qty) / NULLIF(SUM(received_qty), 0) * 100"
        description: "Percentage of received units found defective"
      - name: perfect_order_rate
        expr: "AVG(CASE WHEN is_on_time = 1.0 AND is_in_full = 1.0 AND is_defect_free = 1.0 THEN 1.0 ELSE 0.0 END) * 100"
        description: "Percentage of shipments that are on-time, in-full, and defect-free"

relationships:
  - name: po_to_customer
    left_table: purchase_orders
    right_table: customers
    relationship_columns:
      - left_column: customer_id
        right_column: customer_id
  - name: po_to_plant
    left_table: purchase_orders
    right_table: plants
    relationship_columns:
      - left_column: plant_id
        right_column: plant_id
  - name: po_line_to_po
    left_table: po_lines
    right_table: purchase_orders
    relationship_columns:
      - left_column: po_id
        right_column: po_id
  - name: po_line_to_part
    left_table: po_lines
    right_table: parts
    relationship_columns:
      - left_column: part_id
        right_column: part_id
  - name: shipment_to_po_line
    left_table: shipments
    right_table: po_lines
    relationship_columns:
      - left_column: po_line_id
        right_column: po_line_id
  - name: shipment_to_supplier
    left_table: shipments
    right_table: suppliers
    relationship_columns:
      - left_column: supplier_id
        right_column: supplier_id
  - name: shipment_to_plant
    left_table: shipments
    right_table: plants
    relationship_columns:
      - left_column: plant_id
        right_column: plant_id

# Variables for configurable metric behavior
variables:
  - name: on_time_tolerance_days
    data_type: NUMBER
    default_value: "0"
    description: "Number of days of tolerance for on-time classification. 0 = strict on-or-before-promise."
  - name: fill_rate_method
    data_type: VARCHAR
    default_value: "'ratio'"
    description: "Fill rate calculation method: 'ratio' (qty-based) or 'binary' (line-complete-based)"

module_custom_instructions:
  sql_generation: |
    All percentage metrics (fill_rate, on_time_delivery_rate, otif_rate,
    supplier_defect_rate, perfect_order_rate) are already expressed as
    percentages (0-100). Do NOT multiply by 100 again.

    When a question asks about "supplier performance", default to showing
    fill_rate, on_time_delivery_rate, and supplier_defect_rate grouped by
    supplier_name.

    When a question asks about "delivery performance", default to showing
    on_time_delivery_rate and otif_rate.

    "Lead time" always means avg_lead_time_days unless explicitly qualified.

    For time-based questions without an explicit date range, default to the
    last 12 months.
  question_categorization: |
    If the user asks about inventory levels, stock on hand, or warehouse
    capacity, respond that this semantic view covers order and shipment
    metrics, not inventory snapshots, and suggest they ask about
    inventory_turnover_proxy as an approximation.
```

---

## 6. SYNTHETIC DATA REQUIREMENTS

| Table | Row Count | Rationale |
|---|---|---|
| `suppliers` | 50 | Meaningful grouping by tier/region, still readable |
| `parts` | 200 | ~4 per supplier, 5–6 categories |
| `plants` | 10 | 2–3 per region |
| `customers` | 100 | 3 segments, multiple regions |
| `purchase_orders` | 5,000 | ~50/customer over 18 months |
| `po_lines` | 15,000 | ~3 lines/order (range 1–8) |
| `shipments` | 18,000 | ~1.2/line — partial fills create multi-shipment lines |

**Referential rules:** every FK resolves to a valid PK; `shipped_qty ≤ ordered_qty` for ~85% of shipments (15% over-ship, which is realistic); `delivery_date ≥ ship_date ≥ order_date`.

**Variance targets (to avoid a flat demo):** fill rate 75–98% across suppliers with 2–3 outliers below 70%; on-time 70–95% with a Q4 seasonal dip; defect rate 0.5–5% with one supplier at 8%+; lead time bimodal (domestic 5–10 days, international 20–35) tied to supplier-country vs. plant-country distance. Achieve this by assigning each supplier a hidden "reliability parameter" that drives shipment-level randomness, rather than sampling each field independently — independent per-field randomness is what produces the flat, uncorrelated synthetic data that makes demos unconvincing.

**Generation approach:** Python script generating dimensions first, then a Poisson-process order timeline with seasonality, then per-line shipment counts and per-shipment outcomes driven by supplier reliability parameters, loaded via staged Parquet + `COPY INTO`.

---

## 7. PERSONA QUESTION SET

**Supply Chain Manager:**
1. "What's our overall fill rate this quarter?"
2. "Which suppliers have the worst on-time delivery?"
3. "Show me OTIF trends month over month for the last year"
4. "What's our perfect order rate by region?"
5. "How does lead time compare across our strategic vs. approved suppliers?"
6. "Which plants have the highest defect rates?"
7. "Give me a supplier scorecard for ACME Corp"

**Procurement Analyst:**
1. "What's the fill rate by supplier tier?"
2. "Show me defect rates for critical parts only"
3. "Which suppliers are consistently late?"
4. "What's the average lead time for APAC suppliers vs. EMEA?"
5. "How many purchase orders went to each supplier last quarter?"
6. "What's the total order value by part category?"

**Plant Operations Lead:**
1. "What's the on-time delivery rate for Plant Chicago this month?"
2. "Which parts have the longest lead times coming into my plant?"
3. "Show me the fill rate trend for Plant Chicago"
4. "What's the defect rate by supplier for parts delivered to Plant Chicago?"
5. "How many shipments arrived late last week?"

**Overlapping governance-consistency pairs:**

| Persona A | Persona B | Must resolve to |
|---|---|---|
| "What's our overall fill rate?" | "What percentage of orders are we filling?" | Same `fill_rate` |
| "Which suppliers have the worst on-time delivery?" | "Which suppliers are consistently late?" | Same `on_time_delivery_rate`, supplier grouping |
| "Which plants have the highest defect rates?" | "Defect rate by supplier for my plant?" | Same `supplier_defect_rate` formula, different grouping/filter |
| "Lead time: strategic vs. approved?" | "Lead time: APAC vs. EMEA?" | Same `avg_lead_time_days`, different dimension |
| "Show me OTIF trends month over month" | "Show me the fill rate trend for my plant" | Different metrics, but both correctly use time-series aggregation |

---

## 8. BUILD RISKS AND SEQUENCING

| Risk | Severity | Notes |
|---|---|---|
| Ontology mapping accuracy (1b) | High | The core intellectual problem — automated matching works for well-structured ERPs, struggles with legacy schemas. This is where the generality tax is real. |
| Metrics silently wrong at the wrong grain | High | Mitigated only by the validation gate; the biggest trap is averaging an average. |
| Semantic view token budget | Medium (production only) | Non-issue at 6 tables; split-by-domain if scaling past ~10-15 tables. |
| Verified query maintenance | Medium | Scales linearly with metrics × personas; generate from templates rather than hand-authoring. |
| Flat synthetic data | Medium | Independent per-field sampling is the trap; use correlated per-entity reliability parameters. |
| Cross-metric consistency checks | Low severity, high effort | Getting aggregation grains to line up for `fill_rate * on_time ≈ OTIF` checks is fiddly; get it right early to avoid false validation failures. |

**Where generality is expensive:** the ontology mapping engine and, to a lesser extent, keeping the template engine correct across composite/role-playing table shapes.

**Where generality is cheap:** metric templates, validation-test templates, and persona question templates — all trivially parameterized strings once the pattern exists.

**Recommended sequence:**
1. Synthetic data generation
2. Hand-authored concrete semantic view
3. Verified queries against it
4. Validation suite
5. Extract metric templates/template engine from the concrete view
6. Build the ontology mapping engine last, since it's the heaviest lift and everything before it is independently demonstrable

**First cut if time is short:** drop (5) and (6). Ship the concrete instantiation as proof that governed consistency works, and present the generalization framework as a design backed by that working artifact — this is honest, since the governance story doesn't actually require the automation to exist yet.

---

## 9. PACKAGING FOR REUSE

**CoCo skill: `supply-chain-onboarding`**

- **Inputs:** `source_schema` (DB.SCHEMA), `target_schema`, `target_view_name`, optional `config` YAML for metric variant choices (e.g., `fill_rate_method: binary`) and custom-instruction overrides.
- **Outputs:** `semantic_view.yaml` (deployable), `mapping_report.md` (every mapping decision with confidence score and any human override), `validation_report.md` (pass/fail per test with actuals), `verified_queries.yaml`.

**Automation vs. pause points** (tying back to Sections 1b/1d):

| Phase | Behavior |
|---|---|
| 1a Metadata extraction | Fully automated, no pause |
| 1b Pass 1 (structural) | Fully automated |
| 1b Pass 2 (semantic/LLM) | **Pauses** on any mapping < 0.9 confidence |
| 1b Pass 3 (cross-validation) | **Pauses** on any inconsistency |
| 1c Template instantiation | Fully automated (deterministic substitution) |
| 1d Validation gate | **Pauses** on any failed test, with a fix-mapping-or-accept-deviation decision |

**Required documentation:** privileges needed (SELECT on source, CREATE SEMANTIC VIEW + USAGE on target), the expectation that declared PK/FKs materially improve mapping quality, what the six-entity ontology covers and what happens on partial coverage, how to use the config file for metric variants, how to read confidence scores and common LLM failure patterns (e.g., confusing a supplier-site table for a plant), how to extend the ontology with new entity types, and an explicit limitations section (metrics needing supplementary data, like real inventory turnover, are flagged rather than faked).

**Native App — future direction only, not to design now:** would add persistent mapping/validation history with rollback, multi-tenant onboarding via Marketplace/org listings, a Streamlit UI replacing CLI pause/prompt flows, scheduled re-validation that alerts on metric drift from upstream schema changes, tag-based lineage from metric answers back to source columns for governance audit, and cross-account sharing of the governed semantic view as a data product.

---

This design is grounded in real Snowflake semantic view constructs (YAML spec, variables, derived metrics, verified queries, custom instructions, access modifiers) rather than a generic BI abstraction layer — the framework's "generalization" mechanism (metric templates, ontology mapping) sits entirely outside the semantic view and only touches it via YAML generation, so the resulting artifact is always a valid, auditable Snowflake object.
