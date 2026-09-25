"""
Phase 1a: Metadata Extraction.

Introspects a source schema via Snowflake system views/commands and produces a
manifest.json describing every table (columns, types, PK, FKs, row count, null
rates, cardinality, sample values, date ranges). This is the "framework logic"
half of Phase 1a (design doc Section 1a): mechanical, automatic, no LLM.

Usage:
    python extract_metadata.py --connection UU60334 --source-db SC_DEMO --source-schema RAW --out manifest.json
"""
import argparse
import json
import os
import re

import snowflake.connector

FK_NAME_PATTERN = re.compile(r"(_id|_key|_no|_code)$", re.IGNORECASE)


def rows_as_dicts(cur):
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_tables(cur, db, schema):
    cur.execute(f"SHOW TABLES IN SCHEMA {db}.{schema}")
    return [r["name"] for r in rows_as_dicts(cur)]


def get_columns(cur, db, schema, table):
    cur.execute(f"""
        SELECT column_name, data_type, is_nullable, ordinal_position
        FROM {db}.INFORMATION_SCHEMA.COLUMNS
        WHERE table_schema = '{schema}' AND table_name = '{table}'
        ORDER BY ordinal_position
    """)
    return rows_as_dicts(cur)


def get_primary_keys(cur, db, schema, table):
    try:
        cur.execute(f"SHOW PRIMARY KEYS IN TABLE {db}.{schema}.{table}")
        return [r["column_name"] for r in rows_as_dicts(cur)]
    except Exception:
        return []


def get_foreign_keys(cur, db, schema, table):
    """Returns list of {column, ref_table, ref_column} where `table` holds the FK."""
    try:
        cur.execute(f"SHOW IMPORTED KEYS IN TABLE {db}.{schema}.{table}")
        rows = rows_as_dicts(cur)
        return [
            {
                "column": r["fk_column_name"],
                "ref_database": r["pk_database_name"],
                "ref_schema": r["pk_schema_name"],
                "ref_table": r["pk_table_name"],
                "ref_column": r["pk_column_name"],
            }
            for r in rows
        ]
    except Exception:
        return []


def profile_column(cur, db, schema, table, col_name, data_type):
    """Row count-independent per-column profile: null rate, approx cardinality,
    and for date/numeric columns a min/max; for string/low-cardinality columns,
    top-5 sample values by frequency."""
    fq = f"{db}.{schema}.{table}"
    profile = {}

    cur.execute(f"""
        SELECT
            COUNT(*) AS n,
            COUNT({col_name}) AS non_null,
            APPROX_COUNT_DISTINCT({col_name}) AS approx_distinct
        FROM {fq}
    """)
    row = cur.fetchone()
    n, non_null, approx_distinct = row[0], row[1], row[2]
    profile["row_count"] = n
    profile["null_rate"] = round(1 - (non_null / n), 4) if n else None
    profile["approx_distinct"] = approx_distinct
    profile["approx_cardinality_ratio"] = round(approx_distinct / n, 4) if n else None

    dt = data_type.upper()
    if any(k in dt for k in ("DATE", "TIMESTAMP")):
        cur.execute(f"SELECT MIN({col_name}), MAX({col_name}) FROM {fq}")
        mn, mx = cur.fetchone()
        profile["min"] = str(mn) if mn is not None else None
        profile["max"] = str(mx) if mx is not None else None
    elif any(k in dt for k in ("NUMBER", "FLOAT", "INT", "DECIMAL")):
        cur.execute(f"SELECT MIN({col_name}), MAX({col_name}), AVG({col_name}) FROM {fq}")
        mn, mx, avg = cur.fetchone()
        profile["min"] = float(mn) if mn is not None else None
        profile["max"] = float(mx) if mx is not None else None
        profile["avg"] = float(avg) if avg is not None else None
    else:
        cur.execute(f"""
            SELECT {col_name}, COUNT(*) AS cnt
            FROM {fq}
            WHERE {col_name} IS NOT NULL
            GROUP BY {col_name}
            ORDER BY cnt DESC
            LIMIT 5
        """)
        profile["top_values"] = [str(r[0]) for r in cur.fetchall()]

    return profile


def type_category(data_type):
    dt = data_type.upper()
    if any(k in dt for k in ("NUMBER", "INT", "DECIMAL", "FLOAT", "DOUBLE")):
        return "numeric"
    if any(k in dt for k in ("DATE", "TIMESTAMP", "TIME")):
        return "date"
    if any(k in dt for k in ("TEXT", "VARCHAR", "CHAR", "STRING")):
        return "text"
    return "other"


def infer_foreign_keys(cur, db, schema, manifest):
    """Many source schemas (especially ERP exports) omit declared FK constraints
    (design doc Section 1a: 'many ERP exports omit FKs'). This infers likely FKs
    structurally in two stages:
      1. Naming-convention pre-filter: only columns that look like an FK/ID
         reference (suffix _id/_key/_no/_code) are considered at all. Without
         this, a value-containment check alone produces false positives --
         e.g. a small measure column (capacity_units_per_day) whose value
         range happens to be a numeric subset of a large sequential PK range
         (po_line_id 1..13992) would otherwise "contain-match" spuriously.
      2. Value-containment check: among naming-convention candidates, verify
         the column's non-null values are a subset of some other table's
         single-column primary-key values.
    """
    # Candidate PK targets: table with a single-column PK.
    pk_targets = [
        (t, info["primary_key"][0], type_category(info["columns"][info["primary_key"][0]]["data_type"]))
        for t, info in manifest["tables"].items()
        if len(info["primary_key"]) == 1
    ]

    declared_fk_columns = {
        (t, fk["column"]) for t, info in manifest["tables"].items() for fk in info["foreign_keys"]
    }

    for table, info in manifest["tables"].items():
        for col_name, col_profile in info["columns"].items():
            if (table, col_name) in declared_fk_columns:
                continue
            if info["primary_key"] == [col_name]:
                # Skip only when this column IS the whole (single-column) PK --
                # a table's own identity column can't also be a FK to itself.
                # Composite-PK member columns (e.g. a snapshot/bridge table keyed
                # on (part_id, plant_id)) are legitimate FK candidates and must
                # still be considered.
                continue
            if not FK_NAME_PATTERN.search(col_name):
                continue
            col_cat = type_category(col_profile["data_type"])

            matches = []
            for ref_table, ref_pk_col, ref_pk_cat in pk_targets:
                if ref_table == table:
                    continue
                if ref_pk_cat != col_cat:
                    continue
                cur.execute(f"""
                    SELECT COUNT(*) FROM {db}.{schema}.{table} t
                    WHERE t.{col_name} IS NOT NULL
                      AND NOT EXISTS (
                        SELECT 1 FROM {db}.{schema}.{ref_table} r
                        WHERE r.{ref_pk_col} = t.{col_name}
                      )
                """)
                unmatched = cur.fetchone()[0]
                if unmatched == 0 and col_profile["row_count"] > 0:
                    matches.append((ref_table, ref_pk_col))

            if not matches:
                continue
            if len(matches) == 1:
                best = matches[0]
            else:
                # Multiple tables' PKs fully contain this column's values (common
                # for small-range numeric IDs, e.g. a column ranging 1-10 is a
                # subset of both a 10-row table's PK and a 100-row table's PK).
                # Containment alone is necessary but not sufficient -- break ties
                # by preferring the PK column name that most closely matches the
                # FK column name (e.g. plant_id -> PLANTS.plant_id over
                # plant_id -> CUSTOMERS.customer_id).
                def name_similarity(ref_pk_col):
                    a, b = col_name.lower(), ref_pk_col.lower()
                    if a == b:
                        return 2
                    if a in b or b in a:
                        return 1
                    return 0
                matches.sort(key=lambda m: name_similarity(m[1]), reverse=True)
                best = matches[0]

            ref_table, ref_pk_col = best
            info["foreign_keys"].append({
                "column": col_name,
                "ref_database": db,
                "ref_schema": schema,
                "ref_table": ref_table,
                "ref_column": ref_pk_col,
                "inferred": True,
                "ambiguous_candidates": len(matches) > 1,
            })


def extract_with_cursor(cur, source_db, source_schema):
    """Same as extract() but takes an already-open cursor -- used both by the
    CLI (which opens its own connection) and by the SPCS app (which already
    has a live cursor from its own Snowflake session)."""
    tables = get_tables(cur, source_db, source_schema)
    manifest = {"database": source_db, "schema": source_schema, "tables": {}}

    for table in tables:
        print(f"Profiling {source_db}.{source_schema}.{table} ...")
        columns = get_columns(cur, source_db, source_schema, table)
        pk_cols = get_primary_keys(cur, source_db, source_schema, table)
        fks = get_foreign_keys(cur, source_db, source_schema, table)

        col_profiles = {}
        for col in columns:
            col_profiles[col["COLUMN_NAME"]] = {
                "data_type": col["DATA_TYPE"],
                "is_nullable": col["IS_NULLABLE"] == "YES",
                **profile_column(cur, source_db, source_schema, table, col["COLUMN_NAME"], col["DATA_TYPE"]),
            }

        manifest["tables"][table] = {
            "primary_key": pk_cols,
            "foreign_keys": fks,
            "columns": col_profiles,
        }

    print("Inferring undeclared foreign keys via value-containment check ...")
    infer_foreign_keys(cur, source_db, source_schema, manifest)
    return manifest


def extract(connection_name, source_db, source_schema):
    import sf_connect
    conn = sf_connect.connect(connection_name)
    cur = conn.cursor()
    manifest = extract_with_cursor(cur, source_db, source_schema)
    cur.close()
    conn.close()
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", default="UU60334")
    parser.add_argument("--source-db", required=True)
    parser.add_argument("--source-schema", required=True)
    parser.add_argument("--out", default="manifest.json")
    args = parser.parse_args()

    manifest = extract(args.connection, args.source_db, args.source_schema)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote {args.out} ({len(manifest['tables'])} tables profiled)")
