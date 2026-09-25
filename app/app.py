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

# Real RBAC roles created for this demo (see framework/runs/persona_consistency_check.md):
# each is granted only SELECT on the canonical semantic view (no direct RAW
# table access), so switching between them demonstrates that the SAME
# governed metric resolves identically regardless of which team is asking --
# not just a cosmetic label swap.
PERSONAS = {
    "default": None,
    "planning": "SC_PLANNING_ROLE",
    "procurement": "SC_PROCUREMENT_ROLE",
    "logistics": "SC_LOGISTICS_ROLE",
}
DEFAULT_ROLE = "SUPPLY_CHAIN_APP_ROLE"


@st.cache_resource
def get_cursor():
    conn = sf_session.get_connection()
    return conn.cursor()


def apply_persona(cur, persona):
    """Switches the session's active role to match the selected persona.
    Requires an unrestricted session (the SPCS-deployed app authenticates via
    OAuth as SUPPLY_CHAIN_APP_ROLE, which has all three persona roles granted
    and can freely USE ROLE between them). The local PAT-based dev fallback
    is role-restricted and cannot switch at all -- see
    implementation-findings.md Finding 4 -- so the toggle is disabled there
    instead of failing on every rerun."""
    if not sf_session.is_spcs():
        return
    target_role = PERSONAS[persona] or DEFAULT_ROLE
    if st.session_state.get("_active_role") == target_role:
        return
    try:
        cur.execute(f"USE ROLE {target_role}")
        st.session_state["_active_role"] = target_role
    except Exception as e:
        st.sidebar.warning(f"Could not switch role to {target_role}: {e}")


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
    st.markdown("**Persona**")
    persona = st.radio(
        "Persona", list(PERSONAS.keys()), horizontal=True, label_visibility="collapsed", key="persona_choice",
        disabled=not sf_session.is_spcs(),
    )
    apply_persona(cur, persona)
    if sf_session.is_spcs():
        st.caption(f"role: {PERSONAS[persona] or DEFAULT_ROLE}")
    else:
        st.caption("persona switching needs the deployed app (local dev session can't USE ROLE)")

    st.divider()
    if st.button(":material/add: New chat", use_container_width=True):
        history.clear()
        st.rerun()

tab_ask, tab_onboard = st.tabs([":material/chat: Ask Questions", ":material/build: Onboard Source"])

# st.chat_input only auto-pins to the bottom of the viewport when called at
# the top level of the script -- nested inside st.tabs() it silently falls
# back to rendering inline wherever it's called, which is what produced the
# "input box floats above the newest message" bug. Calling it here (outside
# both tabs) keeps the real pin-to-bottom behavior; the captured value is
# just routed into the Ask Questions tab for processing.
question = st.chat_input("Message the supply chain assistant...")

with tab_ask:
    chat_ui.render_tab(cur, views, selected_view, history, question)

with tab_onboard:
    onboard_ui.render_tab(get_cursor)
