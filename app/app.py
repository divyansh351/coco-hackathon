import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "framework"))

import streamlit as st

import sf_session
import qa
import theme
import chat_ui
import onboard_ui

st.set_page_config(page_title="Supply Chain Semantic Layer", page_icon=":material/terminal:", layout="wide")
theme.inject()


@st.cache_resource
def get_cursor():
    conn = sf_session.get_connection()
    return conn.cursor()


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
    chat_ui.render_tab(cur, views, selected_view, history)

with tab_onboard:
    onboard_ui.render_tab(get_cursor)
