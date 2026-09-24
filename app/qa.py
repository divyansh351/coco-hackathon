"""
"Ask Questions" tab logic: turns a natural-language question into a
SEMANTIC_VIEW SQL query using SNOWFLAKE.CORTEX.AI_COMPLETE, grounded in the
selected semantic view's actual structure (from DESCRIBE SEMANTIC VIEW) --
not the full Cortex Analyst REST API, which would need an External Access
Integration for the container to call out. See framework/README.md and
implementation-findings.md for why.
"""
import json
import re

AI_MODEL = "claude-sonnet-4-5"


def list_semantic_views(cur, database):
    cur.execute(f"SHOW SEMANTIC VIEWS IN DATABASE {database}")
    rows = cur.fetchall()
    cols = [c[0].lower() for c in cur.description]
    views = []
    for row in rows:
        d = dict(zip(cols, row))
        views.append(f"{d['database_name']}.{d['schema_name']}.{d['name']}")
    return views


def describe_semantic_view(cur, view_fqn):
    cur.execute(f"DESCRIBE SEMANTIC VIEW {view_fqn}")
    rows = cur.fetchall()
    cols = [c[0].lower() for c in cur.description]
    return [dict(zip(cols, row)) for row in rows]


def _summarize_structure(desc_rows):
    """Build a compact, LLM-friendly text summary of tables, dimensions,
    facts, metrics, time_dimensions, relationships, and view-level derived
    metrics from DESCRIBE SEMANTIC VIEW output."""
    lines = []
    for row in desc_rows:
        if row["object_kind"] == "EXTENSION" and row["property"] == "VALUE":
            try:
                payload = json.loads(row["property_value"])
            except (ValueError, TypeError):
                continue
            for t in payload.get("tables", []):
                parts = [f"table {t['name']}:"]
                for kind in ("dimensions", "time_dimensions", "facts", "metrics"):
                    names = [x["name"] for x in t.get(kind, [])]
                    if names:
                        parts.append(f"{kind}=[{', '.join(names)}]")
                lines.append(" ".join(parts))
            rels = [r["name"] for r in payload.get("relationships", [])]
            if rels:
                lines.append(f"relationships: {', '.join(rels)}")

    derived = sorted({r["object_name"].lower() for r in desc_rows if r["object_kind"] == "DERIVED_METRIC"})
    if derived:
        lines.append(f"view-level derived metrics (use these names directly, not table-qualified): {', '.join(derived)}")

    return "\n".join(lines)


def _extract_sql(text):
    text = text.strip()
    if text.startswith('"') and text.endswith('"'):
        try:
            text = json.loads(text)
        except (ValueError, TypeError):
            pass
    match = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r"(SELECT\b.*)", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip().rstrip(";")
    raise ValueError(f"No SQL found in LLM response: {text[:300]}")


def ask(cur, view_fqn, question):
    """Returns (generated_sql, result_columns, result_rows)."""
    desc_rows = describe_semantic_view(cur, view_fqn)
    structure = _summarize_structure(desc_rows)

    prompt = f"""You write Snowflake SQL against a semantic view using the
SEMANTIC_VIEW() table function: SELECT ... FROM SEMANTIC_VIEW({view_fqn}
METRICS <metric1>, ... DIMENSIONS <table>.<dimension1>, ...)

Only reference tables, dimensions, facts, metrics, and time_dimensions listed
below -- do not invent columns. Qualify dimension/time_dimension names with
their table name (e.g. suppliers.region) since the same name can exist on
more than one table. View-level derived metrics are referenced by name alone,
not table-qualified.

{structure}

Question: {question}

Reply with ONLY the SQL query, no explanation, no markdown fences."""

    escaped = prompt.replace("'", "''")
    cur.execute(f"SELECT SNOWFLAKE.CORTEX.AI_COMPLETE('{AI_MODEL}', '{escaped}')")
    raw = cur.fetchone()[0]
    sql = _extract_sql(raw)

    cur.execute(sql)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    return sql, cols, rows
