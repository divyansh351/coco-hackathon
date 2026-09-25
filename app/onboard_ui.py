"""
"Onboard Source" tab logic: runs the same 4-phase pipeline as
framework/onboard.py, but in-process against the app's own Snowflake cursor
(no PAT, no subprocess) and returning structured results for Streamlit to
render incrementally. `render_tab()` owns the Streamlit presentation; the
functions above it are pure pipeline logic with no UI dependency.
"""
import json
import os

import pandas as pd
import streamlit as st

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


def render_tab(get_cursor):
    """Renders the full "Onboard Source" tab body. `get_cursor` is a
    zero-arg callable returning a cached Snowflake cursor (kept as a
    parameter so this module has no direct dependency on app.py's caching)."""
    st.subheader("Map a source schema to the ontology and deploy a semantic view")
    st.caption(
        "Runs the ontology-mapping / template-instantiation pipeline "
        "(framework/onboard.py phases 1a-1d) against a source schema, with "
        "zero hand-authored YAML."
    )

    with st.container(border=True):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Source**")
            source_db = st.text_input("Database", value="SC_DEMO", key="src_db")
            source_schema = st.text_input("Schema", value="RAW", key="src_schema")
        with col2:
            st.markdown("**Target**")
            target_db = st.text_input("Database", value="SC_DEMO", key="tgt_db")
            target_schema = st.text_input("Schema", value="ANALYTICS", key="tgt_schema")
        view_name = st.text_input("Semantic view name", value="SUPPLY_CHAIN_ANALYTICS")
        run_clicked = st.button("Run onboarding pipeline", type="primary")

    if not run_clicked:
        return

    cur = get_cursor()
    with st.status("Running onboarding pipeline...", expanded=True) as status:
        def report(msg):
            status.update(label=msg)
            st.write(msg)

        try:
            result = run_onboarding(
                cur, source_db, source_schema, target_db, target_schema, view_name, status_cb=report
            )
        except Exception as e:
            status.update(label="Pipeline failed", state="error")
            st.error(f"Pipeline failed: {e}")
            result = None

        if result and not result.get("error"):
            status.update(label=f"Deployed {result['view_fqn']}", state="complete")
        elif result:
            status.update(label="Mapping rejected -- needs human resolution", state="error")

    if not result:
        return

    if result.get("error"):
        st.error(result["error"])
        with st.expander("Mapping report", expanded=True):
            st.markdown(result["mapping_report"])
        return

    metric_cols = st.columns(len(result["metrics"]) or 1)
    for c, (name, value) in zip(metric_cols, result["metrics"].items()):
        with c:
            st.metric(name.replace("_", " ").title(), round(value, 2) if isinstance(value, float) else value)

    if result["dropped_relationships"]:
        st.info(f"Dropped {len(result['dropped_relationships'])} redundant (denormalized) relationship(s).")

    st.markdown("**Verified query round-trip**")
    vq_df = pd.DataFrame(result["verified_queries"], columns=["query", "result"])
    st.dataframe(vq_df, use_container_width=True, hide_index=True)

    with st.expander("Mapping report"):
        st.markdown(result["mapping_report"])
    with st.expander("Generated semantic view YAML"):
        st.code(result["view_yaml"], language="yaml")
