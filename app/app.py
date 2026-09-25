import sys
import os
import decimal

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
    /* ---- App chrome: hide Streamlit boilerplate for a cleaner "product" feel ---- */
    #MainMenu, footer, [data-testid="stDecoration"] { visibility: hidden; }
    header[data-testid="stHeader"] { background: transparent; }

    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif;
    }

    /* ---- Centered, narrow chat column, like Claude/ChatGPT ---- */
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 6rem;
        max-width: 780px;
        margin: 0 auto;
    }

    h1 { font-weight: 650; font-size: 1.5rem; letter-spacing: -0.01em; }
    .stTabs [data-baseweb="tab-list"] { gap: 1.5rem; justify-content: center; }
    .stTabs [data-baseweb="tab"] { font-weight: 550; }

    /* ---- Chat messages: no boxed bubbles for the assistant, soft pill for the user ---- */
    [data-testid="stChatMessage"] {
        padding: 0.15rem 0;
        margin-bottom: 1.1rem;
        border: none;
        background: transparent;
        gap: 0.75rem;
    }
    [data-testid="stChatMessageAvatarUser"],
    [data-testid="stChatMessageAvatarAssistant"] {
        width: 30px;
        height: 30px;
        font-size: 0.95rem;
    }
    [data-testid="stChatMessageContent"] p { margin-bottom: 0.5rem; line-height: 1.55; }

    /* user turn gets a soft rounded card so it reads as "input", assistant flows freely */
    div[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {
        background: rgba(120, 120, 128, 0.10);
        border-radius: 16px;
        padding: 0.65rem 1rem;
        display: inline-block;
    }

    /* ---- Metric cards ---- */
    div[data-testid="stMetric"] {
        background: rgba(41, 181, 232, 0.07);
        border: 1px solid rgba(41, 181, 232, 0.20);
        border-radius: 14px;
        padding: 0.9rem 1.1rem;
    }

    /* ---- Chat input: rounded pill with soft shadow, ChatGPT/Claude style ---- */
    [data-testid="stChatInput"] {
        border-radius: 24px;
        box-shadow: 0 2px 14px rgba(0, 0, 0, 0.08);
    }
    [data-testid="stChatInput"] textarea { font-size: 0.98rem; }

    /* ---- Suggested-prompt chips on the welcome screen ---- */
    div[class*="st-key-chip_container_"] button {
        border-radius: 999px !important;
        font-size: 0.85rem !important;
        padding: 0.5rem 1rem !important;
        border-color: rgba(120, 120, 128, 0.25) !important;
    }

    /* ---- Buttons generally a touch rounder ---- */
    .stButton button { border-radius: 10px; }

    /* ---- Sidebar ---- */
    section[data-testid="stSidebar"] { border-right: 1px solid rgba(120,120,128,0.15); }
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
        if isinstance(value, decimal.Decimal):
            value = float(value)
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
    with st.chat_message("user", avatar="🧑"):
        st.write(question)
    with st.chat_message("assistant", avatar="✨"):
        if error:
            st.error(error, icon=":material/error:")
            return
        if narrative:
            st.write(narrative)
        render_result(df)
        if sql:
            with st.expander("Show SQL"):
                st.code(sql, language="sql")


def rerun_question(cur, view, question, history):
    with st.spinner("Thinking..."):
        try:
            sql, cols, rows, narrative = qa.ask(cur, view, question, history=history)
            df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
            for c in df.columns:
                if df[c].map(lambda v: isinstance(v, decimal.Decimal)).any():
                    df[c] = df[c].astype(float)
            history.append({"question": question, "sql": sql, "df": df, "narrative": narrative})
        except Exception as e:
            history.append({"question": question, "error": str(e)})


SUGGESTIONS = [
    "What is the overall fill rate?",
    "Which suppliers have the worst on-time delivery rate?",
    "What is our current days of inventory?",
    "What's the average landed cost per unit by supplier region?",
]

with st.sidebar:
    st.markdown("### :material/insights: Supply Chain Analytics")
    st.caption("Governed semantic layer chat + onboarding")

    st.divider()
    st.markdown("**Data source**")
    database_for_search = st.text_input("Database", value="SC_DEMO", label_visibility="collapsed")
    cur = get_cursor()
    try:
        views = qa.list_semantic_views(cur, database_for_search)
    except Exception as e:
        st.error(f"Could not list semantic views: {e}")
        views = []

    selected_view = st.selectbox("Semantic view", views, label_visibility="collapsed") if views else None

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = {}
    if selected_view and selected_view not in st.session_state.chat_history:
        st.session_state.chat_history[selected_view] = []
    history = st.session_state.chat_history.get(selected_view, [])

    st.divider()
    if st.button(":material/add: New chat", use_container_width=True):
        history.clear()
        st.rerun()

tab_ask, tab_onboard = st.tabs([":material/chat: Ask Questions", ":material/build: Onboard Source"])

with tab_ask:
    if not views:
        st.warning("No semantic views found (or none visible to this role).")
    elif not history:
        st.markdown(
            "<div style='text-align:center; padding-top: 4rem;'>"
            "<h2 style='font-weight:600;'>What do you want to know?</h2>"
            f"<p style='color: gray;'>Ask anything about <b>{selected_view.split('.')[-1] if selected_view else ''}</b> "
            "-- grounded in governed metrics, not raw columns.</p></div>",
            unsafe_allow_html=True,
        )
        chip_cols = st.columns(2)
        for i, suggestion in enumerate(SUGGESTIONS):
            with chip_cols[i % 2]:
                with st.container(key=f"chip_container_{i}"):
                    if st.button(suggestion, key=f"chip_{i}", use_container_width=True):
                        rerun_question(cur, selected_view, suggestion, history)
                        st.rerun()

    for i, turn in enumerate(history):
        render_turn(turn["question"], turn.get("sql"), turn.get("df"), turn.get("error"), turn.get("narrative"))
        if turn.get("error"):
            if st.button("Retry this question", key=f"retry_{i}"):
                original_question = turn["question"]
                history.pop(i)
                rerun_question(cur, selected_view, original_question, history)
                st.rerun()

    question = st.chat_input("Message the supply chain assistant...")
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
