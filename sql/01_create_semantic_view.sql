-- ============================================================================
-- DEPRECATED / SUPERSEDED -- kept only as a documented record of the DDL
-- authoring path and the fan-out bug it cannot avoid. The live, correct
-- semantic view is defined in ../semantic_view/supply_chain_analytics.yaml
-- and deployed via SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML. DO NOT RUN THIS
-- FILE against SC_DEMO.ANALYTICS.SUPPLY_CHAIN_ANALYTICS -- it will silently
-- overwrite the view with a WRONG fill_rate metric.
--
-- Why this DDL is wrong: fill_rate = shipments.total_shipped_qty /
-- po_lines.total_ordered_qty combines metrics from two tables with a
-- one-to-many relationship (po_lines -> shipments). DDL's METRICS clause
-- only supports metrics scoped to a single owning table
-- (`<table_alias>.<metric> AS <expr>`); a metric like shipments.fill_rate
-- that references po_lines.total_ordered_qty is evaluated INSIDE the
-- shipments table's own join context, which re-introduces the one-to-many
-- fan-out even though independently SELECTing both metrics side by side
-- does not fan out. Empirically measured on this dataset: the DDL version
-- returned fill_rate = 65.82% while the correct value is 81.76%
-- (3,115,490 shipped / 3,810,616 ordered) -- a ~1.246x inflation matching
-- the shipments-per-po_line ratio (17,430 shipments / 13,992 po_lines).
--
-- The fix requires Snowflake's YAML-only "derived metrics" feature: a
-- `metrics:` block at the TOP LEVEL of the YAML spec (outside `tables:`),
-- which is genuinely scoped to the view rather than to any one table, and
-- correctly combines two independently pre-aggregated table metrics. DDL
-- has no equivalent syntax. See semantic_view/supply_chain_analytics.yaml
-- for the corrected implementation and design doc Section 4/5/8 for the
-- full writeup of this finding.
--
-- A second, unrelated modeling bug found and fixed only in the YAML: this
-- DDL also declares a redundant shipment_to_plant relationship. shipments
-- .plant_id is a denormalized copy of purchase_orders.plant_id, so plants
-- is reachable from shipments via BOTH a direct relationship and the
-- transitive path shipments -> po_lines -> purchase_orders -> plants.
-- Declaring both paths causes Snowflake to reject any query mixing a
-- plant dimension with a shipments-owned metric ("Multi-path relationship
-- ... is not supported"). The YAML version removes the direct
-- relationship and relies on the transitive path only.
-- ============================================================================
--
-- Supply chain semantic view: DDL (SQL) version.
-- Ontology: Supplier -> Part -> Plant -> Shipment -> PurchaseOrder(+Line) -> Customer
-- See ../supply-chain-semantic-framework-design.md Section 5 for the design rationale.

CREATE OR REPLACE SEMANTIC VIEW SC_DEMO.ANALYTICS.SUPPLY_CHAIN_ANALYTICS

  TABLES (
    suppliers AS SC_DEMO.RAW.SUPPLIERS
      PRIMARY KEY (supplier_id)
      WITH SYNONYMS ('vendors')
      COMMENT = 'Suppliers that ship parts to plants against purchase order lines.',
    parts AS SC_DEMO.RAW.PARTS
      PRIMARY KEY (part_id)
      COMMENT = 'Distinct SKUs/components that can be ordered.',
    plants AS SC_DEMO.RAW.PLANTS
      PRIMARY KEY (plant_id)
      COMMENT = 'Manufacturing/assembly facilities that receive shipments and fulfill orders.',
    customers AS SC_DEMO.RAW.CUSTOMERS
      PRIMARY KEY (customer_id)
      COMMENT = 'Customers who place purchase orders against a plant.',
    purchase_orders AS SC_DEMO.RAW.PURCHASE_ORDERS
      PRIMARY KEY (po_id)
      WITH SYNONYMS ('orders', 'POs')
      COMMENT = 'A customer purchase order placed against a plant.',
    po_lines AS SC_DEMO.RAW.PO_LINES
      PRIMARY KEY (po_line_id)
      WITH SYNONYMS ('order lines')
      COMMENT = 'A line item on a purchase order specifying a part and ordered quantity.',
    shipments AS SC_DEMO.RAW.SHIPMENTS
      PRIMARY KEY (shipment_id)
      COMMENT = 'A physical shipment fulfilling (part of) an order line from a supplier to a plant.'
  )

  RELATIONSHIPS (
    po_to_customer AS
      purchase_orders (customer_id) REFERENCES customers,
    po_to_plant AS
      purchase_orders (plant_id) REFERENCES plants,
    po_line_to_po AS
      po_lines (po_id) REFERENCES purchase_orders,
    po_line_to_part AS
      po_lines (part_id) REFERENCES parts,
    shipment_to_po_line AS
      shipments (po_line_id) REFERENCES po_lines,
    shipment_to_supplier AS
      shipments (supplier_id) REFERENCES suppliers,
    shipment_to_plant AS
      shipments (plant_id) REFERENCES plants
  )

  VARIABLES (
    on_time_tolerance_days NUMBER DEFAULT 0
      COMMENT = 'Days of grace period before a late delivery counts as late. 0 = strict on-or-before-promise.'
  )

  FACTS (
    -- ordered_qty must be an explicit fact on po_lines so shipments.is_in_full can
    -- reference it by qualified name (cross-table row-level FK lookup).
    po_lines.ordered_qty AS ordered_qty
      COMMENT = 'Quantity ordered on this purchase order line',

    shipments.is_on_time AS
      CASE WHEN delivery_date <= DATEADD('day', on_time_tolerance_days, promised_delivery_date)
           THEN 1.0 ELSE 0.0 END
      COMMENT = 'Binary: 1 if delivered on or before the promised date (+ tolerance)',
    shipments.is_in_full AS
      CASE WHEN shipped_qty >= po_lines.ordered_qty THEN 1.0 ELSE 0.0 END
      COMMENT = 'Binary: 1 if this shipment alone covers the full ordered quantity of its line',
    shipments.is_defect_free AS
      CASE WHEN defect_qty = 0 THEN 1.0 ELSE 0.0 END
      COMMENT = 'Binary: 1 if no defects were found in this shipment',
    shipments.lead_time_days AS
      DATEDIFF('day', purchase_orders.order_date, delivery_date)
      COMMENT = 'Days from order placement to this shipment''s delivery'
  )

  DIMENSIONS (
    suppliers.supplier_name AS supplier_name
      WITH SYNONYMS = ('vendor name')
      COMMENT = 'Legal name of the supplier',
    suppliers.supplier_country AS country
      COMMENT = 'Country where the supplier is headquartered',
    suppliers.supplier_region AS region
      COMMENT = 'Geographic region: APAC, EMEA, or AMER',
    suppliers.supplier_tier AS tier
      WITH SYNONYMS = ('vendor tier', 'classification')
      COMMENT = 'Strategic classification: Strategic, Preferred, or Approved',

    parts.part_name AS part_name
      COMMENT = 'Human-readable part identifier',
    parts.part_category AS category
      COMMENT = 'Part category, e.g. Electronics, Fasteners, Packaging',
    parts.is_critical_part AS is_critical
      COMMENT = 'Whether this part is classified as supply-critical',

    plants.plant_name AS plant_name
      COMMENT = 'Name of the manufacturing/assembly plant',
    plants.plant_country AS country
      COMMENT = 'Country where the plant is located',
    plants.plant_region AS region
      COMMENT = 'Geographic region of the plant: APAC, EMEA, or AMER',

    customers.customer_name AS customer_name
      COMMENT = 'Name of the customer placing orders',
    customers.customer_segment AS segment
      COMMENT = 'Customer segment: Enterprise, Mid-Market, or SMB',
    customers.customer_region AS region
      COMMENT = 'Geographic region of the customer',

    purchase_orders.po_status AS po_status
      COMMENT = 'Order status: Open, Shipped, Delivered, or Closed',

    purchase_orders.order_date AS order_date
      COMMENT = 'Date the purchase order was placed',
    purchase_orders.requested_delivery_date AS requested_delivery_date
      COMMENT = 'Date the customer requested delivery',
    shipments.ship_date AS ship_date
      COMMENT = 'Date the shipment left the supplier',
    shipments.delivery_date AS delivery_date
      COMMENT = 'Actual date goods were received at the plant',
    shipments.promised_delivery_date AS promised_delivery_date
      COMMENT = 'Date the supplier promised delivery'
  )

  METRICS (
    po_lines.total_ordered_qty AS SUM(po_lines.ordered_qty)
      COMMENT = 'Total quantity ordered across purchase order lines',
    po_lines.total_order_value AS SUM(po_lines.ordered_qty * unit_price)
      COMMENT = 'Total order value (ordered_qty * unit_price)',

    shipments.total_shipped_qty AS SUM(shipped_qty)
      COMMENT = 'Total quantity shipped',
    shipments.total_received_qty AS SUM(received_qty)
      COMMENT = 'Total quantity received',
    shipments.total_defect_qty AS SUM(defect_qty)
      COMMENT = 'Total defective units received',
    shipments.shipment_count AS COUNT(shipment_id)
      COMMENT = 'Number of shipments',
    shipments.on_time_delivery_rate AS AVG(is_on_time) * 100
      COMMENT = 'Percentage of shipments delivered on or before the promised date',
    shipments.otif_rate AS
      AVG(CASE WHEN is_on_time = 1.0 AND is_in_full = 1.0 THEN 1.0 ELSE 0.0 END) * 100
      COMMENT = 'Percentage of shipments that are both on-time and in-full (per-shipment grain)',
    shipments.avg_lead_time_days AS AVG(lead_time_days)
      COMMENT = 'Average days from order placement to delivery',
    shipments.supplier_defect_rate AS
      SUM(defect_qty) / NULLIF(SUM(received_qty), 0) * 100
      COMMENT = 'Percentage of received units found defective',
    shipments.perfect_order_rate AS
      AVG(CASE WHEN is_on_time = 1.0 AND is_in_full = 1.0 AND is_defect_free = 1.0
               THEN 1.0 ELSE 0.0 END) * 100
      COMMENT = 'Percentage of shipments that are on-time, in-full, and defect-free',
    -- Cross-table metric (see correctness note above): combines two independently
    -- table-scoped metrics rather than mixing raw SUMs across the join, avoiding fan-out.
    shipments.fill_rate AS
      shipments.total_shipped_qty / NULLIF(po_lines.total_ordered_qty, 0) * 100
      COMMENT = 'Ratio of total shipped quantity to total ordered quantity, as a percentage',

    -- purchase_orders is at coarser grain than shipments (many shipments per order via
    -- po_lines), so this metric uses nested aggregation: MAX per order, then AVG across orders.
    purchase_orders.order_cycle_time_days AS
      AVG(DATEDIFF('day', purchase_orders.order_date, MAX(shipments.delivery_date)))
      COMMENT = 'Average days from order placement to the last (final) delivery for that order'
  )

  COMMENT = 'Governed supply chain analytics: fill rate, on-time delivery, OTIF, lead time, defect rate, and perfect order rate across suppliers, parts, plants, orders, and customers.'

  AI_SQL_GENERATION '
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
    last 12 months of order_date.
  '

  AI_QUESTION_CATEGORIZATION '
    If the user asks about inventory levels, stock on hand, or warehouse
    capacity, respond that this semantic view covers order and shipment
    metrics, not inventory snapshots, and that no inventory metric is
    currently available in this view.
  ';
