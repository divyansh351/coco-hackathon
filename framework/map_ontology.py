"""
Phase 1b: Ontology Mapping.

Three-pass matching of a source schema (manifest.json from extract_metadata.py)
against the abstract ontology (ontology.json):

  Pass 1 -- Structural signal matching (no LLM): FK-graph position + PK/attribute
            shape scoring. Confidence ~0.4-0.6.
  Pass 2 -- Semantic name matching (LLM-assisted, SNOWFLAKE.CORTEX.AI_COMPLETE):
            for ambiguous/low-confidence entities and attributes, ask an LLM to
            rank candidates given names + sample values + ontology descriptions.
  Pass 3 -- Cross-validation (automated): every ontology relationship must have
            a corresponding FK path; no table double-mapped; every required
            attribute must be mapped. Flags conflicts.

Confidence routing: >=0.9 auto-accept, 0.7-0.9 flagged-but-used, <0.7 unmapped.

Usage:
    python map_ontology.py --manifest manifest.json --ontology ontology.json --out mapping.json
"""
import argparse
import json
import os
import re


# ---------------------------------------------------------------------------
# Pass 1: structural signal matching
# ---------------------------------------------------------------------------

def build_fk_graph(manifest):
    """Returns dict: table -> list of (fk_column, ref_table, ref_column)."""
    graph = {}
    for table, info in manifest["tables"].items():
        graph[table] = [(fk["column"], fk["ref_table"], fk["ref_column"]) for fk in info["foreign_keys"]]
    return graph


def table_role_signal(table, manifest, fk_graph):
    """Structural role classification: how many outgoing FKs (tables this one
    references) vs incoming FKs (tables that reference this one)."""
    out_degree = len(fk_graph[table])
    in_degree = sum(1 for t, fks in fk_graph.items() for (_, ref_t, _) in fks if ref_t == table)
    return {"out_degree": out_degree, "in_degree": in_degree}


def score_table_against_entity(table, manifest, fk_graph, entity):
    """Pass 1 structural score in [0, 1] for how well `table` fits ontology
    `entity`, based on FK graph position + declared-FK count matching the
    entity's expected number of foreign_keys, plus a cheap name-token overlap
    bonus (still "structural", not LLM)."""
    signal = table_role_signal(table, manifest, fk_graph)
    expected_fk_count = len(entity.get("foreign_keys", []))
    actual_fk_count = signal["out_degree"]

    score = 0.0
    # FK-count match is the strongest structural signal (0.0-0.4).
    if expected_fk_count == 0 and actual_fk_count == 0:
        score += 0.4
    elif expected_fk_count > 0 and actual_fk_count >= expected_fk_count:
        score += 0.4 * min(1.0, expected_fk_count / max(actual_fk_count, 1))
    elif expected_fk_count == 0 and actual_fk_count > 0:
        score += 0.1  # entity expects no FKs but table has some -- weak fit

    # Cheap name-token overlap bonus (0.0-0.3): table name vs entity name/aliases.
    names = [entity["name"]] + entity.get("aliases", [])
    table_l = table.lower().replace("_", "")
    best_name_score = 0.0
    for n in names:
        n_l = n.lower()
        if n_l == table_l or n_l + "s" == table_l or n_l == table_l.rstrip("s"):
            best_name_score = 0.3
            break
        if n_l in table_l or table_l in n_l:
            best_name_score = max(best_name_score, 0.15)
    score += best_name_score

    # PK shape bonus (0.0-0.2): single-column PK matching an "id"-like name.
    pk = manifest["tables"][table]["primary_key"]
    if len(pk) == 1 and re.search(r"(_id|_key)$", pk[0], re.IGNORECASE):
        score += 0.1

    return min(1.0, score)


def score_column_against_attribute(col_name, col_profile, attribute):
    """Pass 1 structural score in [0, 1] for how well a column fits an
    ontology attribute, using name/synonym match + type-category agreement."""
    names = [attribute["name"]] + attribute.get("synonyms", [])
    col_l = col_name.lower()
    name_score = 0.0
    for n in names:
        n_l = n.lower()
        if n_l == col_l:
            name_score = 0.6
            break
        if n_l.replace("_", "") == col_l.replace("_", ""):
            name_score = 0.5
            break
        if n_l in col_l or col_l in n_l:
            name_score = max(name_score, 0.3)

    type_score = 0.0
    dt = col_profile["data_type"].upper()
    cat = attribute["type_category"]
    if cat == "id" and any(k in dt for k in ("NUMBER", "INT", "TEXT", "VARCHAR")):
        type_score = 0.2
    elif cat == "numeric" and any(k in dt for k in ("NUMBER", "INT", "FLOAT", "DECIMAL")):
        type_score = 0.2
    elif cat == "date" and any(k in dt for k in ("DATE", "TIMESTAMP")):
        type_score = 0.2
    elif cat == "boolean" and "BOOLEAN" in dt:
        type_score = 0.2
    elif cat == "string" and any(k in dt for k in ("TEXT", "VARCHAR")):
        type_score = 0.2

    return min(1.0, name_score + type_score)


def pass1_structural_matching(manifest, ontology):
    """Returns {entity_name: [(table, score), ...] sorted desc} and
    {(entity_name, attribute_name): [(table, column, score), ...] sorted desc}
    restricted to the top-scoring table candidate per entity."""
    fk_graph = build_fk_graph(manifest)
    tables = list(manifest["tables"].keys())

    entity_table_scores = {}
    for entity in ontology["entities"]:
        scored = [(t, score_table_against_entity(t, manifest, fk_graph, entity)) for t in tables]
        scored.sort(key=lambda x: x[1], reverse=True)
        entity_table_scores[entity["name"]] = scored

    return entity_table_scores, fk_graph


def pass1_attribute_matching(manifest, ontology, entity_to_table):
    """For each (entity, attribute), score all columns of the entity's
    resolved table."""
    results = {}
    for entity in ontology["entities"]:
        table = entity_to_table.get(entity["name"])
        if not table:
            continue
        columns = manifest["tables"][table]["columns"]
        for attribute in entity["attributes"]:
            scored = [
                (col_name, score_column_against_attribute(col_name, col_profile, attribute))
                for col_name, col_profile in columns.items()
            ]
            scored.sort(key=lambda x: x[1], reverse=True)
            results[(entity["name"], attribute["name"])] = scored
    return results


# ---------------------------------------------------------------------------
# Pass 2: semantic name matching (LLM-assisted via SNOWFLAKE.CORTEX.AI_COMPLETE)
# ---------------------------------------------------------------------------

AI_MODEL = "claude-sonnet-4-5"


def _table_summary_for_prompt(manifest, table, max_cols=12):
    info = manifest["tables"][table]
    lines = [f"Table: {table} (primary_key={info['primary_key']}, row_count~{next(iter(info['columns'].values()))['row_count']})"]
    for col_name, prof in list(info["columns"].items())[:max_cols]:
        sample = prof.get("top_values") or [prof.get("min"), prof.get("max")]
        lines.append(f"  - {col_name} ({prof['data_type']}): sample={sample}")
    return "\n".join(lines)


def _extract_json(text):
    """AI_COMPLETE may wrap JSON in prose or markdown fences; extract the first
    top-level {...} block and parse it."""
    text = text.strip()
    # Some connection/auth paths (e.g. PAT-authenticated sessions) return the
    # AI_COMPLETE result double-JSON-encoded -- the whole response wrapped in
    # an outer quoted string with escaped inner quotes/newlines. Unwrap that
    # outer layer first, or the inner {...} would still contain literal
    # backslash-escaped quotes and fail json.loads.
    if text.startswith('"') and text.endswith('"'):
        try:
            text = json.loads(text)
        except (ValueError, TypeError):
            pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in LLM response: {text[:300]}")
    return json.loads(match.group(0))


def call_ai_complete(cur, prompt):
    # Escape single quotes for SQL string literal.
    escaped = prompt.replace("'", "''")
    cur.execute(f"SELECT SNOWFLAKE.CORTEX.AI_COMPLETE('{AI_MODEL}', '{escaped}')")
    return cur.fetchone()[0]


def pass2_llm_entity_matching(cur, manifest, ontology, entity_table_scores, top_n=3, min_gap_to_skip_llm=0.25):
    """For each ontology entity, if Pass 1's top candidate is not decisively
    ahead of the runner-up (gap < min_gap_to_skip_llm), ask the LLM to choose
    among the top-N candidate tables and map attributes to columns. Entities
    with a decisive Pass 1 winner skip the LLM call (saves cost) but still get
    an attribute-mapping LLM call if any required attribute Pass-1-scored low.
    Returns {entity_name: {"table": ..., "confidence": ..., "attribute_mapping": {...}, "rationale": ..., "pass": "1"|"2"}}
    """
    results = {}
    for entity in ontology["entities"]:
        scored = entity_table_scores[entity["name"]]
        top1_table, top1_score = scored[0]
        top2_score = scored[1][1] if len(scored) > 1 else 0.0
        decisive = (top1_score - top2_score) >= min_gap_to_skip_llm and top1_score >= 0.7

        if decisive:
            results[entity["name"]] = {
                "table": top1_table,
                "confidence": top1_score,
                "rationale": f"Pass 1 structural match decisive (score={top1_score:.2f} vs runner-up {top2_score:.2f})",
                "pass": "1",
            }
            continue

        candidates = scored[:top_n]
        candidate_block = "\n\n".join(_table_summary_for_prompt(manifest, t) for t, _ in candidates)
        attrs_block = "\n".join(
            f"  - {a['name']} ({a['type_category']}{', required' if a.get('required') else ''}): synonyms={a.get('synonyms', [])}"
            for a in entity["attributes"]
        )
        prompt = (
            f"You are mapping a physical database schema to an abstract data ontology entity.\n\n"
            f"Ontology entity: {entity['name']} ({entity.get('description', '')})\n"
            f"Expected attributes:\n{attrs_block}\n\n"
            f"Candidate physical tables (ranked by structural heuristics, may be wrong):\n{candidate_block}\n\n"
            f"Pick the single best-matching table for this entity, and map each expected attribute "
            f"name to the physical column name that best represents it (or null if no good match exists).\n"
            f"Reply with ONLY a JSON object, no prose, no markdown fences, in exactly this shape:\n"
            f'{{"table": "<table_name>", "confidence": <0.0-1.0>, '
            f'"attribute_mapping": {{"<attr_name>": "<column_name_or_null>", ...}}, '
            f'"rationale": "<one sentence>"}}'
        )
        raw = call_ai_complete(cur, prompt)
        try:
            parsed = _extract_json(raw)
            parsed["pass"] = "2"
            results[entity["name"]] = parsed
        except Exception as e:
            results[entity["name"]] = {
                "table": top1_table,
                "confidence": top1_score * 0.8,
                "rationale": f"LLM call/parse failed ({e}); fell back to Pass 1 top candidate",
                "pass": "1_fallback",
            }
    return results


def pass2_llm_attribute_matching(cur, manifest, ontology, entity_result):
    """Fill in any attribute not already mapped by the entity-level LLM call
    (pass2_llm_entity_matching only maps attributes when it had to call the
    LLM at all; decisive Pass-1 entities still need per-attribute mapping).
    Uses Pass 1 column scoring; falls back to an LLM call only for attributes
    Pass 1 scored ambiguously (top1-top2 gap small) or not at all.
    """
    for entity in ontology["entities"]:
        res = entity_result.get(entity["name"])
        if not res or not res.get("table"):
            continue
        table = res["table"]
        res.setdefault("attribute_mapping", {})
        columns = manifest["tables"][table]["columns"]

        for attribute in entity["attributes"]:
            if res["attribute_mapping"].get(attribute["name"]):
                continue  # already mapped by entity-level LLM call
            scored = sorted(
                ((c, score_column_against_attribute(c, p, attribute)) for c, p in columns.items()),
                key=lambda x: x[1], reverse=True,
            )
            top1_col, top1_score = scored[0]
            top2_score = scored[1][1] if len(scored) > 1 else 0.0
            if top1_score >= 0.5 and (top1_score - top2_score) >= 0.2:
                res["attribute_mapping"][attribute["name"]] = top1_col
            elif top1_score > 0:
                res["attribute_mapping"][attribute["name"]] = top1_col  # best-effort, low confidence
            else:
                res["attribute_mapping"][attribute["name"]] = None
    return entity_result


# ---------------------------------------------------------------------------
# Pass 3: cross-validation (automated)
# ---------------------------------------------------------------------------

def pass3_cross_validate(manifest, ontology, entity_result):
    """Checks (design doc Section 1b Pass 3):
      - every ontology relationship has a corresponding FK path between the
        resolved tables (in either direction, since the manifest records FKs
        on the child side only)
      - no table is mapped to two different entities
      - every required attribute is mapped to a non-null column
    Returns list of issue dicts: {severity: "error"|"warning", message: str}.
    """
    issues = []

    # No table double-mapped.
    table_to_entities = {}
    for entity_name, res in entity_result.items():
        table_to_entities.setdefault(res["table"], []).append(entity_name)
    for table, entities in table_to_entities.items():
        if len(entities) > 1:
            issues.append({
                "severity": "error",
                "message": f"Table {table} is mapped to multiple entities: {entities}. "
                           f"Ambiguous -- requires human resolution.",
            })

    # Every declared ontology relationship has a corresponding FK path.
    fk_pairs = set()
    for table, info in manifest["tables"].items():
        for fk in info["foreign_keys"]:
            fk_pairs.add((table, fk["ref_table"]))

    entity_by_name = {e["name"]: e for e in ontology["entities"]}
    for entity in ontology["entities"]:
        res = entity_result.get(entity["name"])
        if not res:
            continue
        src_table = res["table"]
        for fk_def in entity.get("foreign_keys", []):
            target_entity_name = fk_def["references_entity"]
            target_res = entity_result.get(target_entity_name)
            if not target_res:
                continue
            target_table = target_res["table"]
            if (src_table, target_table) not in fk_pairs:
                sev = "error" if fk_def.get("required") else "warning"
                issues.append({
                    "severity": sev,
                    "message": f"Ontology relationship {entity['name']} -> {target_entity_name} "
                               f"(resolved: {src_table} -> {target_table}) has no corresponding "
                               f"FK path in the source schema.",
                })

    # Every required attribute must be mapped.
    for entity in ontology["entities"]:
        res = entity_result.get(entity["name"])
        if not res:
            issues.append({"severity": "error", "message": f"Entity {entity['name']} has no table mapping at all."})
            continue
        for attribute in entity["attributes"]:
            if attribute.get("required") and not res["attribute_mapping"].get(attribute["name"]):
                issues.append({
                    "severity": "error",
                    "message": f"Required attribute {entity['name']}.{attribute['name']} "
                               f"(on table {res['table']}) is unmapped.",
                })

    return issues


# ---------------------------------------------------------------------------
# Confidence routing
# ---------------------------------------------------------------------------

def route_confidence(confidence):
    if confidence >= 0.9:
        return "auto_accept"
    if confidence >= 0.7:
        return "flagged_for_review"
    return "requires_human_mapping"


def build_mapping(manifest, ontology, entity_result, cross_validation_issues):
    """Assembles the final mapping.json structure."""
    entities = {}
    for entity_name, res in entity_result.items():
        entities[entity_name] = {
            "table": res["table"],
            "confidence": res["confidence"],
            "action": route_confidence(res["confidence"]),
            "pass": res.get("pass"),
            "rationale": res.get("rationale"),
            "attribute_mapping": res.get("attribute_mapping", {}),
        }
    has_errors = any(i["severity"] == "error" for i in cross_validation_issues)
    return {
        "database": manifest["database"],
        "schema": manifest["schema"],
        "ontology": ontology["name"],
        "entities": entities,
        "cross_validation_issues": cross_validation_issues,
        "overall_status": "REJECTED_NEEDS_HUMAN_RESOLUTION" if has_errors else "OK",
    }


def render_mapping_report(mapping):
    lines = [
        f"# Ontology Mapping Report",
        f"",
        f"Source: `{mapping['database']}.{mapping['schema']}`  |  Ontology: `{mapping['ontology']}`",
        f"Overall status: **{mapping['overall_status']}**",
        f"",
        f"## Entity Mappings",
        f"",
        f"| Entity | Table | Confidence | Action | Pass | Rationale |",
        f"|---|---|---|---|---|---|",
    ]
    for entity_name, e in mapping["entities"].items():
        lines.append(
            f"| {entity_name} | {e['table']} | {e['confidence']:.2f} | {e['action']} | {e['pass']} | {e['rationale']} |"
        )
    lines.append("")
    lines.append("## Attribute Mappings")
    lines.append("")
    for entity_name, e in mapping["entities"].items():
        lines.append(f"**{entity_name}** (`{e['table']}`)")
        for attr, col in e["attribute_mapping"].items():
            lines.append(f"  - `{attr}` -> `{col}`" if col else f"  - `{attr}` -> **UNMAPPED**")
        lines.append("")
    lines.append("## Cross-Validation Issues (Pass 3)")
    lines.append("")
    if not mapping["cross_validation_issues"]:
        lines.append("None -- mapping is internally consistent.")
    else:
        for issue in mapping["cross_validation_issues"]:
            lines.append(f"- **{issue['severity'].upper()}**: {issue['message']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI orchestration
# ---------------------------------------------------------------------------

def run(cur, manifest, ontology):
    entity_table_scores, fk_graph = pass1_structural_matching(manifest, ontology)
    entity_result = pass2_llm_entity_matching(cur, manifest, ontology, entity_table_scores)
    entity_result = pass2_llm_attribute_matching(cur, manifest, ontology, entity_result)
    issues = pass3_cross_validate(manifest, ontology, entity_result)
    mapping = build_mapping(manifest, ontology, entity_result, issues)
    return mapping


if __name__ == "__main__":
    import sf_connect

    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", default="UU60334")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--ontology", default=os.path.join(os.path.dirname(__file__), "ontology.json"))
    parser.add_argument("--out", default="mapping.json")
    parser.add_argument("--report-out", default="mapping_report.md")
    args = parser.parse_args()

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    with open(args.ontology, encoding="utf-8") as f:
        ontology = json.load(f)

    conn = sf_connect.connect(args.connection)
    cur = conn.cursor()
    mapping = run(cur, manifest, ontology)
    cur.close()
    conn.close()

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2)
    with open(args.report_out, "w", encoding="utf-8") as f:
        f.write(render_mapping_report(mapping))
    print(f"Wrote {args.out} and {args.report_out}. Overall status: {mapping['overall_status']}")
