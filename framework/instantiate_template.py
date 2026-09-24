"""
Phase 1c: Template Instantiation.

Reads mapping.json (Phase 1b output) + metric_templates.json +
persona_query_templates.json + ontology.json, resolves the {{Entity.attribute}}
placeholders using the resolved mapping, and emits a complete Snowflake
semantic-view YAML specification (deployable via SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML).

Encodes two correctness rules discovered empirically (implementation-findings.md)
as automated logic rather than manual fixes:
  1. Any metric template with kind="derived" is emitted as a YAML top-level
     derived metric (outside `tables:`), never as a table-scoped metric --
     because it composes metrics owned by two entities connected by a
     one-to-many relationship, which fans out if defined as table-scoped.
  2. Relationship candidates that are reachable via more than one path
     (a direct FK plus a transitive FK chain to the same target table) are
     checked for denormalization (100% sampled-row value agreement between
     the direct FK and the value reachable via the transitive path); if
     confirmed denormalized, only the transitive path is kept.

Usage:
    python instantiate_template.py --mapping mapping.json --out generated_view.yaml
"""
import argparse
import json
import os
import re


PLACEHOLDER_RE = re.compile(r"\{\{(\w+)(?:\.(\w+))?\}\}")


def table_alias(mapping, entity_name):
    return mapping["entities"][entity_name]["table"].lower()


def resolved_column(mapping, entity_name, attr_name):
    col = mapping["entities"][entity_name]["attribute_mapping"].get(attr_name)
    if not col:
        return None
    return col.lower()


def resolve_expr(expr, mapping):
    """Replaces {{Entity.attribute}} with resolved 'table_alias.column' and
    {{Entity}} (no attribute) with just 'table_alias'."""
    def repl(m):
        entity, attr = m.group(1), m.group(2)
        alias = table_alias(mapping, entity)
        if attr:
            col = resolved_column(mapping, entity, attr)
            if col is None:
                raise ValueError(f"Cannot resolve {{{{{entity}.{attr}}}}}: attribute unmapped in mapping.json")
            return f"{alias}.{col}"
        return alias
    return PLACEHOLDER_RE.sub(repl, expr)


def attr_is_mapped(mapping, entity_name, attr_name):
    return bool(mapping["entities"].get(entity_name, {}).get("attribute_mapping", {}).get(attr_name))


# ---------------------------------------------------------------------------
# Relationship resolution + denormalization ("multi-path") detection
# ---------------------------------------------------------------------------

def resolve_relationship_candidates(manifest, mapping, ontology):
    """Builds relationships ONLY from what the ontology explicitly declares
    (entity["foreign_keys"]), matched to a physical FK in the manifest between
    the two resolved tables -- NOT from every incidental FK the extraction
    happened to find between two mapped tables.

    This matters: Phase 1a's FK inference is purely structural and will
    surface real-but-unmodeled relationships (e.g. PARTS.PRIMARY_SUPPLIER_ID
    -> SUPPLIERS, the part's "preferred supplier", found here even though the
    ontology's Part entity declares no foreign_keys at all). Including every
    such incidental FK creates undeclared extra paths between entities and
    breaks Snowflake's semantic view engine, which rejects ANY multi-path
    relationship between two logical tables in a plain DIMENSIONS+METRICS
    query -- even when the two paths are genuinely independent relationships,
    not a denormalization. The ontology is the source of truth for which
    relationships the semantic layer should expose; only relationships it
    declares are instantiated.
    """
    entity_by_table = {e["table"]: name for name, e in mapping["entities"].items()}
    fk_index = {}
    for table, info in manifest["tables"].items():
        for fk in info["foreign_keys"]:
            fk_index.setdefault((table, fk["ref_table"]), []).append(fk)

    candidates = []
    unmatched_ontology_relationships = []
    for entity in ontology["entities"]:
        entity_name = entity["name"]
        res = mapping["entities"].get(entity_name)
        if not res:
            continue
        src_table = res["table"]
        for fk_def in entity.get("foreign_keys", []):
            target_entity_name = fk_def["references_entity"]
            target_res = mapping["entities"].get(target_entity_name)
            if not target_res:
                continue
            target_table = target_res["table"]
            fks = fk_index.get((src_table, target_table), [])
            if not fks:
                unmatched_ontology_relationships.append((entity_name, target_entity_name, src_table, target_table))
                continue
            fk = fks[0]  # if multiple physical FKs exist for one ontology relationship, take the first
            candidates.append({
                "table": src_table,
                "column": fk["column"],
                "ref_table": target_table,
                "ref_column": fk["ref_column"],
                "left_entity": entity_name,
                "right_entity": target_entity_name,
            })
    return candidates, unmatched_ontology_relationships


def find_transitive_path(edges, start, end, exclude_edge):
    """BFS over `edges` (list of dicts with table/ref_table), excluding
    `exclude_edge`, for any directed path start -> ... -> end of length >= 2.
    Returns the path (list of edges) or None."""
    adjacency = {}
    for e in edges:
        if e is exclude_edge:
            continue
        adjacency.setdefault(e["table"], []).append(e)

    # BFS.
    frontier = [(start, [])]
    seen = {start}
    while frontier:
        node, path = frontier.pop(0)
        for e in adjacency.get(node, []):
            if e["ref_table"] == end and len(path) >= 1:
                return path + [e]
            if e["ref_table"] not in seen:
                seen.add(e["ref_table"])
                frontier.append((e["ref_table"], path + [e]))
    return None


def find_multipath_candidates(candidates):
    """Returns list of (direct_edge, transitive_path) for every direct FK
    edge that also has an alternate (>=2 hop) path to the same ref_table."""
    results = []
    for edge in candidates:
        path = find_transitive_path(candidates, edge["table"], edge["ref_table"], exclude_edge=edge)
        if path:
            results.append((edge, path))
    return results


def build_denormalization_check_sql(db, schema, edge, path):
    """SQL to check whether edge.column (on edge.table) always agrees with
    the value reachable via the transitive `path` (a join chain). Returns a
    query whose single output column `mismatches` should be 0 if the direct
    FK is a denormalized copy of the transitively-reachable value."""
    direct = f"t0.{edge['column']}"
    joins = []
    prev_alias = "t0"
    for i, hop in enumerate(path):
        alias = f"t{i+1}"
        joins.append(f"JOIN {db}.{schema}.{hop['ref_table']} {alias} ON {prev_alias}.{hop['column']} = {alias}.{hop['ref_column']}")
        prev_alias = alias
    transitive = f"{prev_alias}.{path[-1]['ref_column']}"
    # The transitive path's final ref_column is the target table's PK; we want
    # the value of the SAME logical column on the target as `edge` points to,
    # i.e. edge['ref_column']. Since path[-1]['ref_table'] == edge['ref_table'],
    # and ref_column there should match edge['ref_column'] for a true comparison.
    sql = f"""
        SELECT COUNT(*) AS mismatches
        FROM {db}.{schema}.{edge['table']} t0
        {' '.join(joins)}
        WHERE {direct} IS NOT NULL AND {direct} != {transitive}
    """
    return sql.strip()


def filter_redundant_relationships(candidates, multipath_pairs, mismatch_counts):
    """Given multipath (direct_edge, transitive_path) pairs and a dict mapping
    id(direct_edge) -> mismatch_count (0 = confirmed denormalized), drops
    confirmed-denormalized direct edges from `candidates`. Returns
    (filtered_candidates, dropped_info)."""
    dropped = []
    to_drop_ids = set()
    for direct_edge, path in multipath_pairs:
        mismatches = mismatch_counts.get(id(direct_edge))
        if mismatches == 0:
            to_drop_ids.add(id(direct_edge))
            dropped.append({
                "table": direct_edge["table"],
                "column": direct_edge["column"],
                "ref_table": direct_edge["ref_table"],
                "reason": "denormalized copy of value reachable via transitive path "
                          + " -> ".join([direct_edge["table"]] + [h["ref_table"] for h in path]),
            })
    filtered = [c for c in candidates if id(c) not in to_drop_ids]
    return filtered, dropped


# ---------------------------------------------------------------------------
# Fact / metric template resolution
# ---------------------------------------------------------------------------

def resolve_facts_by_table(mapping, templates, dims_by_table=None, time_dims_by_table=None):
    """Returns {table_alias: [fact_dict, ...]} where fact_dict has resolved expr.

    Also auto-generates a "passthrough fact" (a bare column exposed as a
    row-level fact) for any {{Entity.attribute}} referenced from ANOTHER
    entity's fact expression (cross_refs), if that attribute isn't already
    addressable as a dimension/time_dimension on its own table. Without this,
    Snowflake rejects the cross-table reference with 'invalid identifier'
    (see implementation-findings.md and design doc Section 8a) -- a numeric
    attribute like ordered_qty is normally only ever used inside SUM(...)
    aggregations, so it has no standalone row-level declaration by default.
    """
    dims_by_table = dims_by_table or {}
    time_dims_by_table = time_dims_by_table or {}
    by_table = {}

    def already_addressable(entity_name, attr_name):
        alias = table_alias(mapping, entity_name)
        col = resolved_column(mapping, entity_name, attr_name)
        if not col:
            return True  # unmapped -- nothing to do, will fail resolution elsewhere
        existing_names = {d["name"] for d in dims_by_table.get(alias, [])} | \
                          {d["name"] for d in time_dims_by_table.get(alias, [])} | \
                          {f["name"] for f in by_table.get(alias, [])}
        return attr_name in existing_names

    for fact in templates["facts"]:
        owning_table = table_alias(mapping, fact["owning_entity"])
        try:
            expr = resolve_expr(fact["expr_template"], mapping)
        except ValueError:
            continue  # unmapped dependency (e.g. optional attribute) -- skip this fact
        by_table.setdefault(owning_table, []).append({
            "name": fact["name"],
            "expr": expr,
            "data_type": fact["data_type"],
            "public": fact.get("public", False),
            "description": fact.get("description", ""),
        })

        for ref in fact.get("cross_refs", []):
            ref_entity, ref_attr = ref["entity"], ref["attribute"]
            if already_addressable(ref_entity, ref_attr):
                continue
            ref_alias = table_alias(mapping, ref_entity)
            ref_col = resolved_column(mapping, ref_entity, ref_attr)
            if not ref_col:
                continue
            by_table.setdefault(ref_alias, []).append({
                "name": ref_attr,
                "expr": ref_col,
                "data_type": "NUMBER",
                "public": False,
                "description": f"Passthrough fact auto-generated so {fact['owning_entity']}.{fact['name']} "
                                f"can reference {ref_entity}.{ref_attr} across tables.",
            })
    return by_table


def _attrs_available(mapping, entity_name, attr_names):
    return all(attr_is_mapped(mapping, entity_name, a.split(".")[-1]) for a in attr_names)


def resolve_metrics(mapping, templates):
    """Returns (table_scoped_metrics_by_table, derived_metrics) where
    table_scoped_metrics_by_table = {table_alias: [metric_dict, ...]}
    derived_metrics = [metric_dict, ...] (view-level, kind='derived')."""
    by_table = {}
    derived = []
    skipped = []

    for metric in templates["metrics"]:
        # Skip metrics whose optional dependencies aren't mapped.
        required_attrs = metric.get("requires_optional_attrs", [])
        if required_attrs:
            ok = True
            for ra in required_attrs:
                entity_name, attr_name = ra.split(".")
                if not attr_is_mapped(mapping, entity_name, attr_name):
                    ok = False
                    break
            if not ok:
                skipped.append({"metric": metric["name"], "reason": f"optional attribute(s) {required_attrs} not mapped"})
                continue

        if metric["kind"] == "derived":
            components_resolved = []
            for comp in metric["components"]:
                alias = table_alias(mapping, comp["entity"])
                components_resolved.append(f"{alias}.{comp['metric']}")
            combine = metric["combine_expr_template"]
            for i, c in enumerate(components_resolved):
                combine = combine.replace(f"{{{i}}}", c)
            derived.append({
                "name": metric["name"],
                "expr": combine,
                "description": metric.get("description", ""),
            })
        else:
            owning_table = table_alias(mapping, metric["owning_entity"])
            try:
                expr = resolve_expr(metric["expr_template"], mapping)
            except ValueError as e:
                skipped.append({"metric": metric["name"], "reason": str(e)})
                continue
            by_table.setdefault(owning_table, []).append({
                "name": metric["name"],
                "expr": expr,
                "description": metric.get("description", ""),
            })

    return by_table, derived, skipped


def resolve_dimensions_by_table(mapping, ontology):
    """Auto-generates DIMENSIONS/time_dimensions from ontology attributes not
    already used as facts. string/boolean -> dimensions; date -> time_dimensions.
    id/numeric attributes are left as plain columns (not surfaced as separate
    dimensions), matching the hand-built view's convention."""
    dims_by_table = {}
    time_dims_by_table = {}
    for entity in ontology["entities"]:
        entity_name = entity["name"]
        res = mapping["entities"].get(entity_name)
        if not res:
            continue
        alias = table_alias(mapping, entity_name)
        for attr in entity["attributes"]:
            col = res["attribute_mapping"].get(attr["name"])
            if not col:
                continue
            col = col.lower()
            entry = {"name": attr["name"], "expr": col, "description": attr.get("description", attr["name"])}
            if attr["type_category"] in ("string", "boolean"):
                dims_by_table.setdefault(alias, []).append(entry)
            elif attr["type_category"] == "date":
                time_dims_by_table.setdefault(alias, []).append(entry)
    return dims_by_table, time_dims_by_table


# ---------------------------------------------------------------------------
# YAML emission
# ---------------------------------------------------------------------------

def yaml_escape(s):
    if s is None:
        return '""'
    if any(c in s for c in [":", "'", '"', "\n"]) or s.strip() != s:
        return json.dumps(s)
    return s


def emit_semantic_view_yaml(view_name, description, mapping, manifest, relationships,
                             facts_by_table, table_metrics_by_table, derived_metrics,
                             dims_by_table, time_dims_by_table, variables):
    lines = []
    lines.append(f"name: {view_name}")
    lines.append(f"description: {yaml_escape(description)}")
    lines.append("tables:")

    for entity_name, res in mapping["entities"].items():
        table = res["table"]
        alias = table.lower()
        pk_cols = manifest["tables"][table]["primary_key"]
        lines.append(f"  - name: {alias}")
        lines.append(f"    base_table:")
        lines.append(f"      database: {mapping['database']}")
        lines.append(f"      schema: {mapping['schema']}")
        lines.append(f"      table: {table}")
        lines.append(f"    primary_key:")
        lines.append(f"      columns: [{', '.join(c.lower() for c in pk_cols)}]")

        dims = dims_by_table.get(alias, [])
        if dims:
            lines.append("    dimensions:")
            for d in dims:
                lines.append(f"      - name: {d['name']}")
                lines.append(f"        expr: {yaml_escape(d['expr'])}")
                lines.append(f"        description: {yaml_escape(d['description'])}")

        tdims = time_dims_by_table.get(alias, [])
        if tdims:
            lines.append("    time_dimensions:")
            for d in tdims:
                lines.append(f"      - name: {d['name']}")
                lines.append(f"        expr: {yaml_escape(d['expr'])}")
                lines.append(f"        description: {yaml_escape(d['description'])}")

        facts = facts_by_table.get(alias, [])
        if facts:
            lines.append("    facts:")
            for f in facts:
                lines.append(f"      - name: {f['name']}")
                lines.append(f"        expr: {yaml_escape(f['expr'])}")
                lines.append(f"        data_type: {f['data_type']}")
                if not f["public"]:
                    lines.append(f"        access_modifier: private_access")
                lines.append(f"        description: {yaml_escape(f['description'])}")

        metrics = table_metrics_by_table.get(alias, [])
        if metrics:
            lines.append("    metrics:")
            for m in metrics:
                lines.append(f"      - name: {m['name']}")
                lines.append(f"        expr: {yaml_escape(m['expr'])}")
                lines.append(f"        description: {yaml_escape(m['description'])}")

    lines.append("relationships:")
    for i, rel in enumerate(relationships):
        left_alias = rel["table"].lower()
        right_alias = rel["ref_table"].lower()
        lines.append(f"  - name: {left_alias}_to_{right_alias}")
        lines.append(f"    left_table: {left_alias}")
        lines.append(f"    right_table: {right_alias}")
        lines.append(f"    relationship_columns:")
        lines.append(f"      - left_column: {rel['column'].lower()}")
        lines.append(f"        right_column: {rel['ref_column'].lower()}")

    if variables:
        lines.append("variables:")
        for v in variables:
            lines.append(f"  - name: {v['name']}")
            lines.append(f"    data_type: {v['data_type']}")
            lines.append(f'    default_value: "{v["default_value"]}"')
            lines.append(f"    description: {yaml_escape(v['description'])}")

    if derived_metrics:
        lines.append("metrics:")
        for m in derived_metrics:
            lines.append(f"  - name: {m['name']}")
            lines.append(f"    expr: {yaml_escape(m['expr'])}")
            lines.append(f"    description: {yaml_escape(m['description'])}")

    return "\n".join(lines)


def resolve_verified_queries(mapping, ontology, query_templates, view_alias):
    """Resolves persona_query_templates.json entries into
    SELECT ... FROM SEMANTIC_VIEW(...) SQL. Skips queries whose
    dimension/metric dependencies aren't available in this instantiation."""
    resolved = []
    for q in query_templates["queries"]:
        if q.get("requires_generated_month_dimension"):
            continue  # not auto-generated in this pass; skip for the generalized template engine
        metrics_clause = ", ".join(q["metrics"])
        dim_clause = ""
        select_cols = list(q["metrics"])
        if "dimension_entity" in q:
            entity_name = q["dimension_entity"]
            res = mapping["entities"].get(entity_name)
            if not res:
                continue
            col_ok = res["attribute_mapping"].get(q["dimension_attribute"])
            if not col_ok:
                continue
            dim_name = q["dimension_attribute"]
            dim_clause = f" DIMENSIONS {dim_name}"
            select_cols = [dim_name] + select_cols
        order_clause = f" ORDER BY {q['order_by']}" if q.get("order_by") else ""
        sql = f"SELECT {', '.join(select_cols)} FROM SEMANTIC_VIEW(__{view_alias}{dim_clause} METRICS {metrics_clause}){order_clause}"
        resolved.append({
            "name": q["name"],
            "question": q["question"],
            "sql": sql,
            "onboarding_question": q.get("onboarding_question", False),
        })
    return resolved


# ---------------------------------------------------------------------------
# CLI orchestration
# ---------------------------------------------------------------------------

FRAMEWORK_DIR = os.path.dirname(__file__)


def run(cur, mapping, manifest, ontology, templates, query_templates, view_name, target_db, target_schema):
    candidates, unmatched = resolve_relationship_candidates(manifest, mapping, ontology)
    multipath = find_multipath_candidates(candidates)

    mismatch_counts = {}
    denorm_log = []
    for direct_edge, path in multipath:
        sql = build_denormalization_check_sql(mapping["database"], mapping["schema"], direct_edge, path)
        cur.execute(sql)
        mismatches = cur.fetchone()[0]
        mismatch_counts[id(direct_edge)] = mismatches
        denorm_log.append({
            "direct": f"{direct_edge['table']}.{direct_edge['column']} -> {direct_edge['ref_table']}",
            "transitive_path": " -> ".join([direct_edge["table"]] + [h["ref_table"] for h in path]),
            "mismatches": mismatches,
            "verdict": "denormalized (dropping direct edge)" if mismatches == 0 else "genuinely independent relationship (keeping both)",
        })

    relationships, dropped = filter_redundant_relationships(candidates, multipath, mismatch_counts)

    dims_by_table, time_dims_by_table = resolve_dimensions_by_table(mapping, ontology)
    facts_by_table = resolve_facts_by_table(mapping, templates, dims_by_table, time_dims_by_table)
    table_metrics_by_table, derived_metrics, skipped_metrics = resolve_metrics(mapping, templates)

    view_yaml = emit_semantic_view_yaml(
        view_name=view_name,
        description=f"Auto-generated by the ontology-mapping/template-instantiation engine from {mapping['database']}.{mapping['schema']} against ontology {mapping['ontology']}.",
        mapping=mapping, manifest=manifest, relationships=relationships,
        facts_by_table=facts_by_table, table_metrics_by_table=table_metrics_by_table,
        derived_metrics=derived_metrics, dims_by_table=dims_by_table,
        time_dims_by_table=time_dims_by_table, variables=templates.get("variables", []),
    )

    verified_queries = resolve_verified_queries(mapping, ontology, query_templates, view_name.lower())

    return {
        "view_yaml": view_yaml,
        "denormalization_log": denorm_log,
        "dropped_relationships": dropped,
        "skipped_metrics": skipped_metrics,
        "verified_queries": verified_queries,
    }


if __name__ == "__main__":
    import sf_connect

    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", default="UU60334")
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--ontology", default=os.path.join(FRAMEWORK_DIR, "ontology.json"))
    parser.add_argument("--metric-templates", default=os.path.join(FRAMEWORK_DIR, "metric_templates.json"))
    parser.add_argument("--query-templates", default=os.path.join(FRAMEWORK_DIR, "persona_query_templates.json"))
    parser.add_argument("--view-name", required=True)
    parser.add_argument("--target-db", required=True)
    parser.add_argument("--target-schema", required=True)
    parser.add_argument("--out", default="generated_view.yaml")
    args = parser.parse_args()

    with open(args.mapping, encoding="utf-8") as f:
        mapping = json.load(f)
    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    with open(args.ontology, encoding="utf-8") as f:
        ontology = json.load(f)
    with open(args.metric_templates, encoding="utf-8") as f:
        templates = json.load(f)
    with open(args.query_templates, encoding="utf-8") as f:
        query_templates = json.load(f)

    conn = sf_connect.connect(args.connection)
    cur = conn.cursor()
    result = run(cur, mapping, manifest, ontology, templates, query_templates,
                 args.view_name, args.target_db, args.target_schema)
    cur.close()
    conn.close()

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(result["view_yaml"])
    print(f"Wrote {args.out}")
    print(f"Dropped {len(result['dropped_relationships'])} redundant relationship(s), "
          f"skipped {len(result['skipped_metrics'])} metric(s) due to unmapped optional attrs.")
    for d in result["denormalization_log"]:
        print(" -", d)
