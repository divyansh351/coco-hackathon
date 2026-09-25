"""
Synthetic data generator for the governed supply chain semantic layer demo.

Generates 7 entities (suppliers, parts, plants, customers, purchase_orders,
po_lines, shipments) with correlated per-supplier reliability parameters so
that downstream metrics (fill rate, on-time rate, defect rate) show realistic,
non-flat variance -- per Section 6 of supply-chain-semantic-framework-design.md.

Usage:
    python generate_synthetic_data.py            # writes CSVs to ./data/
    python generate_synthetic_data.py --load     # also loads directly into
                                                   # Snowflake via the active
                                                   # `cortex` CLI connection
"""
import argparse
import csv
import os
import random
import statistics
from collections import defaultdict
from datetime import date, timedelta

SEED = 42
START_DATE = date(2025, 4, 1)
END_DATE = date(2026, 9, 24)
TOTAL_DAYS = (END_DATE - START_DATE).days

REGIONS = ["APAC", "EMEA", "AMER"]
COUNTRIES = {
    "APAC": ["China", "Vietnam", "India", "Japan", "South Korea"],
    "EMEA": ["Germany", "Poland", "UK", "Italy", "Turkey"],
    "AMER": ["USA", "Mexico", "Canada", "Brazil"],
}
TIERS = ["Strategic", "Preferred", "Approved"]
TIER_WEIGHTS = [0.2, 0.35, 0.45]
CATEGORIES = ["Electronics", "Fasteners", "Packaging", "Raw Materials", "Machined Components", "Textiles"]
PLANT_LOCATIONS = [
    ("Chicago", "USA", "AMER"), ("Monterrey", "Mexico", "AMER"),
    ("Shenzhen", "China", "APAC"), ("Bangalore", "India", "APAC"), ("Osaka", "Japan", "APAC"),
    ("Stuttgart", "Germany", "EMEA"), ("Katowice", "Poland", "EMEA"), ("Manchester", "UK", "EMEA"),
    ("Sao Paulo", "Brazil", "AMER"), ("Izmir", "Turkey", "EMEA"),
]
SEGMENTS = ["Enterprise", "Mid-Market", "SMB"]
SEGMENT_WEIGHTS = [0.25, 0.4, 0.35]

N_SUPPLIERS = 50
N_PARTS = 200
N_CUSTOMERS = 100
N_POS = 5000
OUTLIER_SUPPLIER_IDS = [5, 23, 41]     # forced below 70% fill rate
HIGH_DEFECT_SUPPLIER = 17              # forced >= 8% defect rate


def gen_suppliers(rng):
    suppliers = []
    for sid in range(1, N_SUPPLIERS + 1):
        region = rng.choice(REGIONS)
        country = rng.choice(COUNTRIES[region])
        tier = rng.choices(TIERS, weights=TIER_WEIGHTS)[0]
        base = {"Strategic": 0.90, "Preferred": 0.83, "Approved": 0.78}[tier]
        reliability = min(0.99, max(0.55, rng.gauss(base, 0.08)))
        contracted_lead = {
            "APAC": rng.randint(18, 35),
            "EMEA": rng.randint(10, 22),
            "AMER": rng.randint(5, 14),
        }[region]
        suppliers.append({
            "supplier_id": sid,
            "supplier_name": f"{country.split()[0]}Supply-{sid:03d}",
            "country": country,
            "region": region,
            "tier": tier,
            "lead_time_days_contracted": contracted_lead,
            "reliability_score": round(reliability, 3),
        })
    for outlier_id in OUTLIER_SUPPLIER_IDS:
        suppliers[outlier_id - 1]["reliability_score"] = round(rng.uniform(0.55, 0.68), 3)
    return suppliers


def gen_parts(rng, n_suppliers):
    parts = []
    for pid in range(1, N_PARTS + 1):
        category = rng.choice(CATEGORIES)
        parts.append({
            "part_id": pid,
            "part_name": f"{category[:4].upper()}-{pid:04d}",
            "category": category,
            "unit_cost": round(rng.uniform(0.5, 500), 2),
            "weight_kg": round(rng.uniform(0.01, 50), 2),
            "is_critical": rng.random() < 0.15,
            "primary_supplier_id": rng.randint(1, n_suppliers),
        })
    return parts


def gen_plants(rng):
    return [
        {
            "plant_id": i,
            "plant_name": f"Plant {name}",
            "country": country,
            "region": region,
            "capacity_units_per_day": rng.randint(500, 5000),
        }
        for i, (name, country, region) in enumerate(PLANT_LOCATIONS, start=1)
    ]


def gen_customers(rng):
    customers = []
    for cid in range(1, N_CUSTOMERS + 1):
        region = rng.choice(REGIONS)
        segment = rng.choices(SEGMENTS, weights=SEGMENT_WEIGHTS)[0]
        customers.append({
            "customer_id": cid,
            "customer_name": f"{segment.replace('-', '')}Cust-{cid:04d}",
            "segment": segment,
            "region": region,
        })
    return customers


def seasonal_weight(d):
    """Slight upward trend + Q4 spike + weekday bias."""
    trend = 1.0 + 0.15 * ((d - START_DATE).days / TOTAL_DAYS)
    q4_boost = 1.4 if d.month in (10, 11, 12) else 1.0
    weekday_factor = 0.3 if d.weekday() >= 5 else 1.0
    return trend * q4_boost * weekday_factor


def gen_purchase_orders(rng, customers, plants):
    all_days = [START_DATE + timedelta(days=i) for i in range(TOTAL_DAYS + 1)]
    weights = [seasonal_weight(d) for d in all_days]
    order_dates = sorted(rng.choices(all_days, weights=weights, k=N_POS))

    statuses = ["Open", "Shipped", "Delivered", "Closed"]
    purchase_orders = []
    for i, od in enumerate(order_dates, start=1):
        customer = rng.choice(customers)
        if rng.random() < 0.6:
            same_region = [p for p in plants if p["region"] == customer["region"]]
            plant = rng.choice(same_region) if same_region else rng.choice(plants)
        else:
            plant = rng.choice(plants)
        requested_delivery = od + timedelta(days=rng.randint(10, 45))
        days_ago = (END_DATE - od).days
        if days_ago < 10:
            status = rng.choices(statuses, weights=[0.5, 0.35, 0.1, 0.05])[0]
        elif days_ago < 30:
            status = rng.choices(statuses, weights=[0.05, 0.25, 0.4, 0.3])[0]
        else:
            status = rng.choices(statuses, weights=[0.0, 0.02, 0.08, 0.9])[0]
        purchase_orders.append({
            "po_id": i,
            "customer_id": customer["customer_id"],
            "plant_id": plant["plant_id"],
            "order_date": od,
            "requested_delivery_date": requested_delivery,
            "po_status": status,
        })
    return purchase_orders


def gen_po_lines(rng, purchase_orders, parts):
    po_lines = []
    po_line_id = 1
    lines_choices = [1, 2, 3, 4, 5, 6, 7, 8]
    lines_weights = [0.20, 0.28, 0.24, 0.14, 0.08, 0.03, 0.02, 0.01]
    for po in purchase_orders:
        n_lines = rng.choices(lines_choices, weights=lines_weights)[0]
        chosen_parts = rng.sample(parts, min(n_lines, len(parts)))
        for part in chosen_parts:
            ordered_qty = round(rng.choice([10, 20, 50, 100, 200, 500, 1000]) * rng.uniform(0.5, 1.5))
            unit_price = round(part["unit_cost"] * rng.uniform(1.15, 1.6), 2)
            po_lines.append({
                "po_line_id": po_line_id,
                "po_id": po["po_id"],
                "part_id": part["part_id"],
                "ordered_qty": ordered_qty,
                "unit_price": unit_price,
            })
            po_line_id += 1
    return po_lines


def gen_shipments(rng, po_lines, parts_by_id, po_by_id, suppliers_by_id, n_suppliers):
    shipments = []
    shipment_id = 1
    shipment_count_choices = [1, 2, 3]
    shipment_count_weights = [0.80, 0.15, 0.05]
    # Region-driven freight/duty overhead per unit -- APAC/EMEA suppliers incur
    # higher landed-cost overhead than AMER (longer routes, import duties).
    FREIGHT_PER_UNIT = {"APAC": 1.35, "EMEA": 0.85, "AMER": 0.35}
    DUTY_RATE = {"APAC": 0.06, "EMEA": 0.04, "AMER": 0.015}

    for line in po_lines:
        part = parts_by_id[line["part_id"]]
        po = po_by_id[line["po_id"]]
        supplier_id = part["primary_supplier_id"] if rng.random() < 0.85 else rng.randint(1, n_suppliers)
        supplier = suppliers_by_id[supplier_id]
        reliability = supplier["reliability_score"]

        # Metric levels are driven directly off the supplier's hidden reliability
        # parameter (rather than sampled independently per field) so that a
        # supplier's fill rate, on-time rate, and defect rate move together --
        # producing the correlated variance real supply chains show, instead of
        # flat/uncorrelated noise.
        expected_fill_frac = min(1.02, max(0.50, 1.03 - (1 - reliability) * 1.15))
        expected_otr = min(0.97, max(0.55, 1.0 - (1 - reliability) * 0.70))
        expected_defect_frac = max(0.003, (1 - reliability) * 0.12)
        if supplier_id == HIGH_DEFECT_SUPPLIER:
            expected_defect_frac = max(expected_defect_frac, 0.085)

        n_ship = rng.choices(shipment_count_choices, weights=shipment_count_weights)[0]
        remaining_qty = line["ordered_qty"]
        promised_delivery = po["order_date"] + timedelta(days=supplier["lead_time_days_contracted"])

        for s in range(n_ship):
            is_last = (s == n_ship - 1)
            if n_ship == 1 or is_last:
                qty_alloc = remaining_qty
            else:
                qty_alloc = round(remaining_qty * rng.uniform(0.3, 0.6))
                remaining_qty -= qty_alloc

            ship_offset = max(1, int(rng.gauss(supplier["lead_time_days_contracted"] * 0.5, 3)))
            ship_date = po["order_date"] + timedelta(days=ship_offset)

            # Q4 (Nov/Dec) seasonal on-time dip
            seasonal_otr = max(0.35, expected_otr - (0.15 if ship_date.month in (11, 12) else 0.0))
            on_time = rng.random() < seasonal_otr
            if on_time:
                delivery_date = max(ship_date, promised_delivery - timedelta(days=rng.randint(0, 3)))
            else:
                delivery_date = promised_delivery + timedelta(days=rng.randint(1, 12))
            if delivery_date < ship_date:
                delivery_date = ship_date

            fill_frac = min(1.08, max(0.40, rng.gauss(expected_fill_frac, 0.05)))
            shipped_qty = round(qty_alloc * fill_frac)
            received_qty = shipped_qty

            defect_frac = min(0.5, max(0.0, rng.gauss(expected_defect_frac, expected_defect_frac * 0.6)))
            defect_qty = min(received_qty, round(received_qty * defect_frac))

            region = supplier["region"]
            freight_cost = round(shipped_qty * FREIGHT_PER_UNIT[region] * rng.uniform(0.85, 1.15), 2)
            duty_cost = round(shipped_qty * line["unit_price"] * DUTY_RATE[region] * rng.uniform(0.85, 1.15), 2)

            shipments.append({
                "shipment_id": shipment_id,
                "po_line_id": line["po_line_id"],
                "supplier_id": supplier_id,
                "plant_id": po["plant_id"],
                "ship_date": ship_date,
                "delivery_date": delivery_date,
                "promised_delivery_date": promised_delivery,
                "shipped_qty": shipped_qty,
                "received_qty": received_qty,
                "defect_qty": defect_qty,
                "freight_cost": freight_cost,
                "duty_cost": duty_cost,
            })
            shipment_id += 1
    return shipments


def gen_inventory(rng, shipments, po_lines_by_id):
    """One snapshot row per (part, plant) that has shipment history, sized off
    that part's observed shipped-qty rate over the window so days_of_inventory
    lands in a plausible 15-45 day range rather than being arbitrary."""
    shipped_by_part_plant = defaultdict(float)
    for s in shipments:
        part_id = po_lines_by_id[s["po_line_id"]]["part_id"]
        shipped_by_part_plant[(part_id, s["plant_id"])] += s["shipped_qty"]

    inventory = []
    for (part_id, plant_id), total_shipped in shipped_by_part_plant.items():
        avg_daily_shipped_qty = total_shipped / TOTAL_DAYS
        quantity_on_hand = max(1, round(avg_daily_shipped_qty * rng.uniform(15, 45)))
        inventory.append({
            "part_id": part_id,
            "plant_id": plant_id,
            "quantity_on_hand": quantity_on_hand,
            "as_of_date": END_DATE,
        })
    return inventory


def validate(suppliers, parts, plants, customers, purchase_orders, po_lines, shipments, inventory):
    """Referential integrity + metric-variance sanity checks (Phase 1d style)."""
    supplier_ids = {s["supplier_id"] for s in suppliers}
    part_ids = {p["part_id"] for p in parts}
    plant_ids = {p["plant_id"] for p in plants}
    customer_ids = {c["customer_id"] for c in customers}
    po_ids = {po["po_id"] for po in purchase_orders}
    po_line_ids = {l["po_line_id"] for l in po_lines}

    assert all(po["customer_id"] in customer_ids for po in purchase_orders)
    assert all(po["plant_id"] in plant_ids for po in purchase_orders)
    assert all(l["po_id"] in po_ids for l in po_lines)
    assert all(l["part_id"] in part_ids for l in po_lines)
    assert all(s["po_line_id"] in po_line_ids for s in shipments)
    assert all(s["supplier_id"] in supplier_ids for s in shipments)
    assert all(s["plant_id"] in plant_ids for s in shipments)
    assert all(s["delivery_date"] >= s["ship_date"] for s in shipments)
    assert all(s["freight_cost"] >= 0 and s["duty_cost"] >= 0 for s in shipments)
    assert all(i["part_id"] in part_ids and i["plant_id"] in plant_ids for i in inventory)
    assert all(i["quantity_on_hand"] > 0 for i in inventory)
    assert len(inventory) > 0
    print("Referential integrity: PASS")

    line_ordered = {l["po_line_id"]: l["ordered_qty"] for l in po_lines}
    by_supplier = defaultdict(list)
    for s in shipments:
        by_supplier[s["supplier_id"]].append(s)

    def supplier_fill_rate(ships):
        total_shipped = sum(s["shipped_qty"] for s in ships)
        distinct_lines = {s["po_line_id"] for s in ships}
        total_ordered = sum(line_ordered[lid] for lid in distinct_lines)
        return total_shipped / total_ordered * 100 if total_ordered else None

    fills = [supplier_fill_rate(ships) for ships in by_supplier.values()]
    otrs = [sum(1 for s in ships if s["delivery_date"] <= s["promised_delivery_date"]) / len(ships) * 100
            for ships in by_supplier.values()]
    defects = [sum(s["defect_qty"] for s in ships) / max(1, sum(s["received_qty"] for s in ships)) * 100
               for ships in by_supplier.values()]

    print(f"Fill rate  : min={min(fills):.1f} max={max(fills):.1f} mean={statistics.mean(fills):.1f}")
    print(f"On-time    : min={min(otrs):.1f} max={max(otrs):.1f} mean={statistics.mean(otrs):.1f}")
    print(f"Defect rate: min={min(defects):.2f} max={max(defects):.2f} mean={statistics.mean(defects):.2f}")

    total_shipped_all = sum(s["shipped_qty"] for s in shipments)
    total_on_hand = sum(i["quantity_on_hand"] for i in inventory)
    implied_doi = total_on_hand / (total_shipped_all / TOTAL_DAYS)
    print(f"Inventory  : {len(inventory)} (part,plant) rows, implied days_of_inventory~={implied_doi:.1f}")


def write_csvs(out_dir, suppliers, parts, plants, customers, purchase_orders, po_lines, shipments, inventory):
    os.makedirs(out_dir, exist_ok=True)
    tables = {
        "suppliers.csv": (suppliers, ["supplier_id", "supplier_name", "country", "region", "tier",
                                       "lead_time_days_contracted", "reliability_score"]),
        "parts.csv": (parts, ["part_id", "part_name", "category", "unit_cost", "weight_kg",
                               "is_critical", "primary_supplier_id"]),
        "plants.csv": (plants, ["plant_id", "plant_name", "country", "region", "capacity_units_per_day"]),
        "customers.csv": (customers, ["customer_id", "customer_name", "segment", "region"]),
        "purchase_orders.csv": (purchase_orders, ["po_id", "customer_id", "plant_id", "order_date",
                                                   "requested_delivery_date", "po_status"]),
        "po_lines.csv": (po_lines, ["po_line_id", "po_id", "part_id", "ordered_qty", "unit_price"]),
        "shipments.csv": (shipments, ["shipment_id", "po_line_id", "supplier_id", "plant_id", "ship_date",
                                       "delivery_date", "promised_delivery_date", "shipped_qty",
                                       "received_qty", "defect_qty", "freight_cost", "duty_cost"]),
        "inventory.csv": (inventory, ["part_id", "plant_id", "quantity_on_hand", "as_of_date"]),
    }
    for filename, (rows, fields) in tables.items():
        path = os.path.join(out_dir, filename)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row[k] for k in fields})
        print(f"Wrote {len(rows)} rows -> {path}")


def generate_all(seed=SEED):
    rng = random.Random(seed)
    suppliers = gen_suppliers(rng)
    parts = gen_parts(rng, N_SUPPLIERS)
    plants = gen_plants(rng)
    customers = gen_customers(rng)
    purchase_orders = gen_purchase_orders(rng, customers, plants)
    po_lines = gen_po_lines(rng, purchase_orders, parts)

    parts_by_id = {p["part_id"]: p for p in parts}
    po_by_id = {po["po_id"]: po for po in purchase_orders}
    suppliers_by_id = {s["supplier_id"]: s for s in suppliers}
    shipments = gen_shipments(rng, po_lines, parts_by_id, po_by_id, suppliers_by_id, N_SUPPLIERS)

    po_lines_by_id = {l["po_line_id"]: l for l in po_lines}
    inventory = gen_inventory(rng, shipments, po_lines_by_id)

    return suppliers, parts, plants, customers, purchase_orders, po_lines, shipments, inventory


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=os.path.join(os.path.dirname(__file__), "..", "data"))
    args = parser.parse_args()

    data = generate_all()
    validate(*data)
    write_csvs(args.out_dir, *data)
