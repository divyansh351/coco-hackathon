import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "framework"))

import pandas as pd
import streamlit as st

import sf_session
import qa
import onboard_ui

st.set_page_config(page_title="Supply Chain Semantic Layer", page_icon=":material/insights:", layout="wide")

st.markdown(
    """
    <style>
    .block-container { padding-top: 2rem; max-width: 1100px; }
    [data-testid="stChatMessage"] { padding: 0.9rem 1.1rem; border-radius: 14px; margin-bottom: 0.6rem; }
    [data-testid="stChatMessageContent"] p { margin-bottom: 0.4rem; }
    div[data-testid="stMetric"] {
        background: rgba(41, 181, 232, 0.08);
        border: 1px solid rgba(41, 181, 232, 0.25);
        border-radius: 12px;
        padding: 0.8rem 1rem;
    }
    h1 { font-weight: 650; }
    .stTabs [data-baseweb="tab-list"] { gap: 1.5rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_cursor():
    conn = sf_session.get_connection()
    return conn.cursor()


def render_result(df):
    """Render a query result compactly: a single scalar as a metric card,
    otherwise a table (+ a bar chart when the shape fits)."""
    if df is None or df.empty:
        st.caption("Query returned no rows.")
        return
    if df.shape == (1, 1):
        col = df.columns[0]
        value = df.iloc[0, 0]
        if isinstance(value, float):
            value = round(value, 2)
        st.metric(col.replace("_", " ").title(), value)
        return
    st.dataframe(df, use_container_width=True, hide_index=True)
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    if len(df) > 1 and len(numeric_cols) >= 1 and len(df.columns) - len(numeric_cols) == 1:
        label_col = [c for c in df.columns if c not in numeric_cols][0]
        st.bar_chart(df.set_index(label_col)[numeric_cols])


def render_turn(question, sql=None, df=None, error=None, narrative=None):
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        if error:
            st.error(error, icon=":material/error:")
            return
        if narrative:
            st.write(narrative)
        render_result(df)
        with st.expander("Generated SQL"):
            st.code(sql, language="sql")


def rerun_question(cur, view, question, history):
    with st.spinner("Generating and running SQL..."):
        try:
            sql, cols, rows, narrative = qa.ask(cur, view, question, history=history)
            df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
            history.append({"question": question, "sql": sql, "df": df, "narrative": narrative})
        except Exception as e:
            history.append({"question": question, "error": str(e)})


st.title("Supply Chain Semantic Layer")
st.caption("Ontology-mapping onboarding pipeline + a chat interface over your deployed semantic views.")

tab_ask, tab_onboard = st.tabs(["Ask Questions", "Onboard Source"])

with tab_ask:
    with st.sidebar:
        st.header("Ask Questions")
        database_for_search = st.text_input("Database", value="SC_DEMO")
        cur = get_cursor()
        try:
            views = qa.list_semantic_views(cur, database_for_search)
        except Exception as e:
            st.error(f"Could not list semantic views: {e}")
            views = []

        selected_view = st.selectbox("Semantic view", views) if views else None

        if "chat_history" not in st.session_state:
            st.session_state.chat_history = {}
        if selected_view and selected_view not in st.session_state.chat_history:
            st.session_state.chat_history[selected_view] = []
        history = st.session_state.chat_history.get(selected_view, [])

        if st.button("Clear conversation", use_container_width=True):
            history.clear()
            st.rerun()

    if not views:
        st.warning("No semantic views found (or none visible to this role).")
    elif not history:
        st.info(
            f"Ask a question about **{selected_view}** below. Try: "
            "\"What is the overall fill rate?\" or \"Which suppliers have the worst on-time delivery rate?\""
        )

    for i, turn in enumerate(history):
        render_turn(turn["question"], turn.get("sql"), turn.get("df"), turn.get("error"), turn.get("narrative"))
        if turn.get("error"):
            if st.button("Retry this question", key=f"retry_{i}"):
                original_question = turn["question"]
                history.pop(i)
                rerun_question(cur, selected_view, original_question, history)
                st.rerun()

    question = st.chat_input("Ask a question...")
    if question and selected_view:
        rerun_question(cur, selected_view, question, history)
        st.rerun()

with tab_onboard:
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

    if run_clicked:
        cur = get_cursor()
        with st.status("Running onboarding pipeline...", expanded=True) as status:
            def report(msg):
                status.update(label=msg)
                st.write(msg)

            try:
                result = onboard_ui.run_onboarding(
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

        if result:
            if result.get("error"):
                st.error(result["error"])
                with st.expander("Mapping report", expanded=True):
                    st.markdown(result["mapping_report"])
            else:
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
