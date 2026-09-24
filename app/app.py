import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "framework"))

import pandas as pd
import streamlit as st

import sf_session
import qa
import onboard_ui

st.set_page_config(page_title="Supply Chain Semantic Layer", layout="wide")


@st.cache_resource
def get_cursor():
    conn = sf_session.get_connection()
    return conn.cursor()


st.title("Supply Chain Semantic Layer")

tab_onboard, tab_ask = st.tabs(["Onboard Source", "Ask Questions"])

with tab_onboard:
    st.subheader("Map a source schema to the ontology and deploy a semantic view")
    st.caption(
        "Runs the ontology-mapping / template-instantiation pipeline "
        "(framework/onboard.py phases 1a-1d) against a source schema, with "
        "zero hand-authored YAML."
    )
    col1, col2 = st.columns(2)
    with col1:
        source_db = st.text_input("Source database", value="SC_DEMO")
        source_schema = st.text_input("Source schema", value="RAW")
    with col2:
        target_db = st.text_input("Target database", value="SC_DEMO")
        target_schema = st.text_input("Target schema", value="ANALYTICS_AUTO")
    view_name = st.text_input("Semantic view name", value="SUPPLY_CHAIN_ANALYTICS_AUTO")

    if st.button("Run onboarding pipeline", type="primary"):
        cur = get_cursor()
        status_box = st.empty()

        def report(msg):
            status_box.info(msg)

        with st.spinner("Running..."):
            try:
                result = onboard_ui.run_onboarding(
                    cur, source_db, source_schema, target_db, target_schema, view_name, status_cb=report
                )
            except Exception as e:
                st.error(f"Pipeline failed: {e}")
                result = None

        if result:
            if result.get("error"):
                st.error(result["error"])
                with st.expander("Mapping report", expanded=True):
                    st.markdown(result["mapping_report"])
            else:
                status_box.success(f"Deployed {result['view_fqn']}")
                st.write(result["deploy_status"])

                st.subheader("Metric values")
                st.dataframe(pd.DataFrame([result["metrics"]]))

                if result["dropped_relationships"]:
                    st.info(f"Dropped {len(result['dropped_relationships'])} redundant (denormalized) relationship(s).")

                st.subheader("Verified query round-trip")
                vq_df = pd.DataFrame(result["verified_queries"], columns=["query", "result"])
                st.dataframe(vq_df, use_container_width=True)

                with st.expander("Mapping report"):
                    st.markdown(result["mapping_report"])
                with st.expander("Generated semantic view YAML"):
                    st.code(result["view_yaml"], language="yaml")

with tab_ask:
    st.subheader("Ask a question about a deployed semantic view")
    cur = get_cursor()

    database_for_search = st.text_input("Database to search for semantic views", value="SC_DEMO")
    try:
        views = qa.list_semantic_views(cur, database_for_search)
    except Exception as e:
        st.error(f"Could not list semantic views: {e}")
        views = []

    if not views:
        st.warning("No semantic views found (or none visible to this role).")
    else:
        selected_view = st.selectbox("Semantic view", views)
        question = st.text_input(
            "Question",
            placeholder="e.g. What is the overall fill rate?",
        )
        if st.button("Ask", type="primary") and question:
            with st.spinner("Generating and running SQL..."):
                try:
                    sql, cols, rows = qa.ask(cur, selected_view, question)
                except Exception as e:
                    st.error(f"Could not answer: {e}")
                    sql, cols, rows = None, None, None

            if sql:
                with st.expander("Generated SQL", expanded=False):
                    st.code(sql, language="sql")
                if rows:
                    df = pd.DataFrame(rows, columns=cols)
                    st.dataframe(df, use_container_width=True)
                    numeric_cols = df.select_dtypes(include="number").columns.tolist()
                    if len(df) > 1 and len(numeric_cols) >= 1 and len(df.columns) - len(numeric_cols) == 1:
                        label_col = [c for c in df.columns if c not in numeric_cols][0]
                        st.bar_chart(df.set_index(label_col)[numeric_cols])
                else:
                    st.info("Query returned no rows.")
