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
    """Build a compact, LLM-friendly text summary of tables, their PUBLIC
    dimensions/facts/metrics, relationships, and view-level derived metrics.
    PRIVATE facts (e.g. is_defect_free, lead_time_days) exist only to compose
    metric expressions internally -- they are not valid in a DIMENSIONS(...)/
    FACTS(...) clause and must be excluded, or the LLM will hallucinate a
    query against them and get an invalid-identifier error."""
    grouped = {}
    for row in desc_rows:
        if row["object_kind"] not in ("DIMENSION", "FACT", "METRIC"):
            continue
        key = (row["object_kind"], row["object_name"], row["parent_entity"])
        grouped.setdefault(key, {})[row["property"]] = row["property_value"]

    tables = {}
    for (kind, name, table), props in grouped.items():
        if props.get("ACCESS_MODIFIER") != "PUBLIC":
            continue
        bucket = {"DIMENSION": "dimensions", "FACT": "facts", "METRIC": "metrics"}[kind]
        tables.setdefault(table, {"dimensions": [], "facts": [], "metrics": []})[bucket].append(name.lower())

    lines = []
    for table, kinds in sorted(tables.items()):
        parts = [f"table {table.lower()}:"]
        for bucket in ("dimensions", "facts", "metrics"):
            if kinds[bucket]:
                parts.append(f"{bucket}=[{', '.join(sorted(kinds[bucket]))}]")
        lines.append(" ".join(parts))

    for row in desc_rows:
        if row["object_kind"] == "EXTENSION" and row["property"] == "VALUE":
            try:
                payload = json.loads(row["property_value"])
            except (ValueError, TypeError):
                continue
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


def ask(cur, view_fqn, question, history=None):
    """history: optional list of {"question": ..., "sql": ...} from earlier
    turns in the same chat, so follow-up questions ("and by region?") can be
    resolved with context. Returns (generated_sql, result_columns, result_rows)."""
    desc_rows = describe_semantic_view(cur, view_fqn)
    structure = _summarize_structure(desc_rows)

    history_block = ""
    successful_turns = [h for h in (history or []) if h.get("sql")]
    if successful_turns:
        turns = "\n".join(f"- Q: {h['question']}\n  SQL: {h['sql']}" for h in successful_turns[-5:])
        history_block = f"\nPrevious turns in this conversation (for context on follow-up questions):\n{turns}\n"

    prompt = f"""You write Snowflake SQL against a semantic view using the
SEMANTIC_VIEW() table function: SELECT ... FROM SEMANTIC_VIEW({view_fqn}
METRICS <metric1>, ... DIMENSIONS <table>.<dimension1>, ...)

Only reference tables, dimensions, facts, and metrics listed below, using the
EXACT names shown -- do not invent, pluralize, abbreviate, or guess variants
of a name (e.g. the list below might say `segment`, not `customer_segment` --
use exactly what's listed). Inside the METRICS(...)/DIMENSIONS(...) clauses,
qualify dimension names with their table name (e.g. suppliers.region) since
the same name can exist on more than one table. View-level derived metrics
are referenced by name alone, not table-qualified. Only names listed below
under a table are queryable -- there may be other columns on the underlying
physical table that are NOT part of this semantic view and must not be used.

If you need an outer WHERE/ORDER BY/GROUP BY on the query result (e.g. to
filter or sort), reference the OUTPUT column by its bare name only (e.g.
`order_date`, never `purchase_orders.order_date`) -- SEMANTIC_VIEW() returns
unqualified column names, and a table-qualified name anywhere outside the
SEMANTIC_VIEW(...) parentheses is an invalid identifier. This applies to the
outer SELECT list too: prefer `SELECT * FROM SEMANTIC_VIEW(...)` and put every
metric/dimension you want in the METRICS(...)/DIMENSIONS(...) clauses, rather
than writing your own outer SELECT column list with table-qualified names.

{structure}
{history_block}
Question: {question}

If the question is a follow-up (e.g. "and by region?", "what about last month?"),
resolve it using the previous turns above into a complete, standalone query.

Reply with ONLY the SQL query, no explanation, no markdown fences."""

    escaped = prompt.replace("'", "''")
    cur.execute(f"SELECT SNOWFLAKE.CORTEX.AI_COMPLETE('{AI_MODEL}', '{escaped}')")
    raw = cur.fetchone()[0]
    sql = _extract_sql(raw)

    cur.execute(sql)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    return sql, cols, rows
