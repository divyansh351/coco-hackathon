"""
"Onboard Source" tab logic: runs the same 4-phase pipeline as
framework/onboard.py, but in-process against the app's own Snowflake cursor
(no PAT, no subprocess) and returning structured results for Streamlit to
render incrementally.
"""
import json
import os

import extract_metadata
import map_ontology
import instantiate_template as it

FRAMEWORK_DIR = os.path.join(os.path.dirname(__file__), "..", "framework")


def load_json(name):
    with open(os.path.join(FRAMEWORK_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def run_onboarding(cur, source_db, source_schema, target_db, target_schema, view_name, status_cb=None):
    """status_cb(str) is called with progress messages if provided.
    Returns a dict with manifest, mapping, mapping_report, view_yaml,
    validation results, or {"error": ..., "mapping_report": ...} if the
    mapping was rejected before deployment."""
    def status(msg):
        if status_cb:
            status_cb(msg)

    ontology = load_json("ontology.json")
    templates = load_json("metric_templates.json")
    query_templates = load_json("persona_query_templates.json")

    status(f"Extracting metadata from {source_db}.{source_schema} ...")
    manifest = extract_metadata.extract_with_cursor(cur, source_db, source_schema)

    status("Mapping source schema to ontology (3-pass, LLM-assisted) ...")
    mapping = map_ontology.run(cur, manifest, ontology)
    mapping_report = map_ontology.render_mapping_report(mapping)

    if mapping["overall_status"] != "OK":
        return {
            "error": "Mapping rejected by cross-validation -- needs human resolution.",
            "manifest": manifest,
            "mapping": mapping,
            "mapping_report": mapping_report,
        }

    status("Instantiating templates into a concrete semantic view YAML ...")
    result = it.run(cur, mapping, manifest, ontology, templates, query_templates,
                     view_name, target_db, target_schema)

    status(f"Deploying to {target_db}.{target_schema} ...")
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {target_db}.{target_schema}")
    yaml_str = result["view_yaml"]
    cur.execute(f"CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('{target_db}.{target_schema}', $${yaml_str}$$)")
    deploy_status = cur.fetchone()[0]

    status("Running validation gate ...")
    view_fqn = f"{target_db}.{target_schema}.{view_name}"
    cur.execute(f"SELECT * FROM SEMANTIC_VIEW({view_fqn} METRICS fill_rate, on_time_delivery_rate, "
                f"otif_rate, perfect_order_rate, supplier_defect_rate, avg_lead_time_days, order_cycle_time_days)")
    cols = [c[0] for c in cur.description]
    row = cur.fetchone()
    metrics_out = dict(zip(cols, row)) if row else {}

    vq_results = []
    for vq in result["verified_queries"]:
        try:
            cur.execute(vq["sql"].replace(f"__{view_name.lower()}", view_fqn))
            rows = cur.fetchall()
            vq_results.append((vq["name"], "PASS" if rows else "FAIL (empty)"))
        except Exception as e:
            vq_results.append((vq["name"], f"FAIL ({e})"))

    return {
        "manifest": manifest,
        "mapping": mapping,
        "mapping_report": mapping_report,
        "view_yaml": yaml_str,
        "view_fqn": view_fqn,
        "deploy_status": deploy_status,
        "metrics": metrics_out,
        "verified_queries": vq_results,
        "dropped_relationships": result.get("dropped_relationships", []),
    }
