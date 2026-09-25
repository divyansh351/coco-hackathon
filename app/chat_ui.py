"""
"Ask Questions" tab UI: rendering and state management for the chat
interface. Talks to Snowflake only through `qa.ask()` -- no SQL or agent
logic lives here, just Streamlit presentation.
"""
import decimal

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import qa

SUGGESTIONS = [
    "What is the overall fill rate?",
    "Which suppliers have the worst on-time delivery rate?",
    "What is our current days of inventory?",
    "What's the average landed cost per unit by supplier region?",
]


def _scroll_to_bottom():
    """New turns render after the (visually fixed, but DOM-flow-last) chat
    input, so a fresh message can land directly under the pinned bottom bar
    with no scroll to reveal it. Force the page to the bottom explicitly
    rather than relying on Streamlit's default scroll behavior."""
    components.html(
        "<script>"
        "window.parent.document.documentElement.scrollTo(0, window.parent.document.documentElement.scrollHeight);"
        "window.parent.document.body.scrollTo(0, window.parent.document.body.scrollHeight);"
        "</script>",
        height=0,
    )


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
    with st.chat_message("user"):
        st.caption("you")
        st.write(question)
    with st.chat_message("assistant"):
        st.caption("assistant")
        if error:
            st.error(error, icon=":material/error:")
            return
        if narrative:
            st.write(narrative)
        if sql:
            render_result(df)
            with st.expander("show sql"):
                st.code(sql, language="sql")


def rerun_question(cur, view, question, history):
    """Renders the user's message immediately (so it doesn't appear to
    "disappear" while the query runs), then runs it under a spinner and
    appends the result to history."""
    with st.chat_message("user"):
        st.caption("you")
        st.write(question)
    _scroll_to_bottom()
    with st.chat_message("assistant"):
        st.caption("assistant")
        with st.spinner("Thinking..."):
            try:
                sql, cols, rows, narrative = qa.ask(cur, view, question, history=history)
                df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
                for c in df.columns:
                    if df[c].map(lambda v: isinstance(v, decimal.Decimal)).any():
                        df[c] = df[c].astype(float)
                history.append({"question": question, "sql": sql, "df": df, "narrative": narrative})
                if narrative:
                    st.write(narrative)
                if sql:
                    render_result(df)
                    with st.expander("show sql"):
                        st.code(sql, language="sql")
            except Exception as e:
                history.append({"question": question, "error": str(e)})
                st.error(str(e), icon=":material/error:")
    _scroll_to_bottom()


def render_tab(cur, views, selected_view, history, question):
    """Renders the "Ask Questions" tab body: welcome screen with suggestion
    chips when empty, then the message history. `question` is the value
    already captured from a top-level `st.chat_input()` call in app.py (see
    that file for why it can't be called from inside this tab)."""
    if not views:
        st.warning("No semantic views found (or none visible to this role).")
    elif not history and not question:
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
                if st.button(suggestion, key=f"chip_{i}", use_container_width=True):
                    rerun_question(cur, selected_view, suggestion, history)
                    st.session_state["_just_answered"] = True
                    st.rerun()

    for i, turn in enumerate(history):
        render_turn(turn["question"], turn.get("sql"), turn.get("df"), turn.get("error"), turn.get("narrative"))
        if turn.get("error"):
            if st.button("Retry this question", key=f"retry_{i}"):
                original_question = turn["question"]
                history.pop(i)
                rerun_question(cur, selected_view, original_question, history)
                st.session_state["_just_answered"] = True
                st.rerun()

    if st.session_state.pop("_just_answered", False):
        _scroll_to_bottom()

    if question and selected_view:
        rerun_question(cur, selected_view, question, history)
        st.session_state["_just_answered"] = True
        st.rerun()
