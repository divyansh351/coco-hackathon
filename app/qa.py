"""
"Ask Questions" tab logic: turns a natural-language question into an answer
grounded in the canonical semantic view.

Primary path: SNOWFLAKE.CORTEX.DATA_AGENT_RUN against a real Cortex Agent
(SC_DEMO.APP.SUPPLY_CHAIN_AGENT) whose tool is a genuine
cortex_analyst_text_to_sql tool bound to the semantic view. This runs entirely
inside Snowflake's SQL engine -- no External Access Integration, no REST call,
no network egress from the container required (see implementation-findings.md).

Fallback path: if the agent call fails for any reason (agent not found,
permission issue, parsing failure), fall back to the original AI_COMPLETE +
DESCRIBE SEMANTIC VIEW approach so the demo keeps working end to end.
"""
import json
import re

AI_MODEL = "claude-sonnet-4-5"
AGENT_FQN = "SC_DEMO.APP.SUPPLY_CHAIN_AGENT"


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


# ---------------------------------------------------------------------------
# Primary path: real Cortex Agent via DATA_AGENT_RUN
# ---------------------------------------------------------------------------

_TYPE_MAP = {"fixed": float, "real": float, "text": str, "boolean": bool, "date": str, "timestamp_ntz": str}


def _cast_row_value(raw, sql_type):
    if raw is None:
        return None
    caster = _TYPE_MAP.get(sql_type, str)
    try:
        return caster(raw)
    except (TypeError, ValueError):
        return raw


def _ask_via_agent(cur, agent_fqn, question, history=None):
    """Calls SNOWFLAKE.CORTEX.DATA_AGENT_RUN and extracts (sql, cols, rows,
    narrative) from the response. Raises on any unexpected shape so the
    caller can fall back to the AI_COMPLETE path."""
    successful_turns = [h for h in (history or []) if not h.get("error")]
    text = question
    if successful_turns:
        context = "; ".join(f"Q: {h['question']}" for h in successful_turns[-5:])
        text = f"(Earlier in this conversation: {context}) {question}"

    payload = json.dumps({"messages": [{"role": "user", "content": [{"type": "text", "text": text}]}]})
    escaped_agent = agent_fqn.replace("'", "''")
    escaped_payload = payload.replace("'", "''")
    cur.execute(
        f"SELECT SNOWFLAKE.CORTEX.DATA_AGENT_RUN('{escaped_agent}', '{escaped_payload}', TRUE)"
    )
    raw = cur.fetchone()[0]
    data = json.loads(raw) if isinstance(raw, str) else raw

    sql = None
    cols = None
    rows = None
    narrative_parts = []

    # The agent can self-correct: try SQL, get an error back, retry with
    # different SQL, and eventually succeed -- all within the same response.
    # Only the LAST successful system_execute_sql result should be used as
    # the answer; an earlier failed attempt must not abort parsing (it isn't
    # the final outcome), so intermediate tool errors are tracked but not
    # raised immediately.
    content = data.get("content", [])
    last_tool_error = None
    for i, block in enumerate(content):
        btype = block.get("type")
        if btype == "text":
            narrative_parts.append(block["text"])
        elif btype == "tool_use" and block.get("tool_use", {}).get("name") == "system_execute_sql":
            candidate_sql = block["tool_use"]["input"].get("sql")
            # The matching tool_result is typically the very next block.
            for candidate in content[i + 1:i + 3]:
                if candidate.get("type") != "tool_result":
                    continue
                for c in candidate["tool_result"].get("content", []):
                    j = c.get("json", {})
                    if "error" in j:
                        last_tool_error = j["error"]
                        continue
                    result_set = j.get("result_set")
                    if result_set:
                        row_type = result_set.get("resultSetMetaData", {}).get("rowType", [])
                        sql = candidate_sql
                        cols = [rt["name"] for rt in row_type]
                        types = [rt.get("type", "text") for rt in row_type]
                        rows = [
                            tuple(_cast_row_value(v, t) for v, t in zip(r, types))
                            for r in result_set.get("data", [])
                        ]
                        last_tool_error = None
                break

    if sql is None:
        # The agent answered without needing to run SQL (e.g. an opinion/
        # advice question like "what would you suggest to improve this?").
        # That's a legitimate response, not a failure -- don't fall back to
        # AI_COMPLETE, which would force-generate SQL for a non-SQL question.
        narrative = "\n\n".join(narrative_parts).strip()
        if not narrative:
            raise ValueError("DATA_AGENT_RUN response contained neither SQL nor a text answer")
        return None, None, None, narrative

    if cols is None or rows is None:
        raise ValueError("DATA_AGENT_RUN response referenced SQL but no completed tool result was found")

    narrative = "\n\n".join(narrative_parts).strip()
    return sql, cols, rows, narrative


# ---------------------------------------------------------------------------
# Fallback path: AI_COMPLETE + DESCRIBE SEMANTIC VIEW prompt engineering
# ---------------------------------------------------------------------------

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
        sql = match.group(1).strip()
    else:
        match = re.search(r"(SELECT\b.*)", text, re.DOTALL | re.IGNORECASE)
        if not match:
            raise ValueError(f"No SQL found in LLM response: {text[:300]}")
        sql = match.group(1).strip().rstrip(";")

    # The model occasionally hallucinates an empty METRICS()/DIMENSIONS()
    # clause for questions that don't need one (e.g. a plain aggregate with
    # no breakdown) -- "DIMENSIONS )" is a Snowflake syntax error, not just
    # a no-op, so strip it defensively rather than relying on prompt wording
    # alone to prevent it every time.
    sql = re.sub(r"\b(METRICS|DIMENSIONS)\s*\)", ")", sql, flags=re.IGNORECASE)
    return sql


def _ask_via_ai_complete(cur, view_fqn, question, history=None):
    """history: optional list of {"question": ..., "sql": ...} from earlier
    turns in the same chat, so follow-up questions ("and by region?") can be
    resolved with context. Returns (generated_sql, result_columns, result_rows, narrative)."""
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

If the question does NOT require querying data (e.g. it's an opinion,
explanation, or "what would you suggest" question), do not invent a query --
reply with exactly `NO_SQL_NEEDED:` followed by a short plain-text answer.

If the question has no natural breakdown (e.g. a single overall metric with
no "by X"), do NOT include an empty `DIMENSIONS()` clause -- omit the
DIMENSIONS clause entirely rather than writing `DIMENSIONS )` with nothing
inside it.

Otherwise, reply with ONLY the SQL query, no explanation, no markdown fences."""

    escaped = prompt.replace("'", "''")
    cur.execute(f"SELECT SNOWFLAKE.CORTEX.AI_COMPLETE('{AI_MODEL}', '{escaped}')")
    raw = cur.fetchone()[0]
    raw_text = raw.strip()
    if raw_text.startswith('"') and raw_text.endswith('"'):
        try:
            raw_text = json.loads(raw_text)
        except (ValueError, TypeError):
            pass
    if raw_text.strip().startswith("NO_SQL_NEEDED:"):
        return None, None, None, raw_text.split("NO_SQL_NEEDED:", 1)[1].strip()

    sql = _extract_sql(raw)

    cur.execute(sql)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    return sql, cols, rows, ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def ask(cur, view_fqn, question, history=None, agent_fqn=AGENT_FQN):
    """Returns (generated_sql, result_columns, result_rows, narrative).
    Tries the real Cortex Agent (DATA_AGENT_RUN) first; falls back to the
    AI_COMPLETE prompt-engineering approach if the agent call fails."""
    try:
        return _ask_via_agent(cur, agent_fqn, question, history=history)
    except Exception:
        return _ask_via_ai_complete(cur, view_fqn, question, history=history)
