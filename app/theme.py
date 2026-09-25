"""
Visual theme for the Streamlit app: a plain terminal look -- dark
background, monospace font, terracotta accent used only for text/borders on
functional elements (never decorative boxes). Kept separate from app.py so
page wiring and visual styling can change independently.
"""
import streamlit as st

ACCENT = "#D97757"       # Claude's terracotta accent
BG = "#171716"           # near-black warm charcoal
TEXT = "#e8e4de"
MUTED = "#8a8680"

_CSS = f"""
<style>
/* ---- App chrome: hide Streamlit boilerplate ---- */
#MainMenu, footer, [data-testid="stDecoration"] {{ visibility: hidden; }}
header[data-testid="stHeader"] {{ background: transparent; }}

/* ---- Terminal aesthetic: monospace everywhere, no decorative boxes ---- */
html, body, [class*="css"], input, textarea, button {{
    font-family: "Cascadia Code", "JetBrains Mono", "Fira Code", Consolas, "Courier New", monospace !important;
}}

.stApp {{ background-color: {BG}; color: {TEXT}; }}

.block-container {{
    padding-top: 1.5rem;
    padding-bottom: 6rem;
    max-width: 860px;
    margin: 0 auto;
}}

h1, h2, h3 {{ color: {TEXT}; font-weight: 600; }}
h1 {{ font-size: 1.3rem; }}

.stTabs [data-baseweb="tab-list"] {{ gap: 1.5rem; justify-content: center; border-bottom: 1px solid rgba(217,119,87,0.15); }}
.stTabs [data-baseweb="tab"] {{ font-weight: 550; color: {MUTED}; }}
.stTabs [aria-selected="true"] {{ color: {ACCENT} !important; }}

/* ---- Chat messages: plain terminal-output flow, no boxes ---- */
[data-testid="stChatMessage"] {{
    padding: 0.2rem 0;
    margin-bottom: 0.3rem;
    border: none;
    background: transparent;
    gap: 0.6rem;
}}
[data-testid="stChatMessageAvatarUser"],
[data-testid="stChatMessageAvatarAssistant"] {{
    display: none;
}}
[data-testid="stChatMessageContent"] p {{
    margin-bottom: 0.4rem;
    line-height: 1.55;
    color: {TEXT};
}}
[data-testid="stChatMessageContent"] code {{
    color: {ACCENT};
    background: transparent;
}}
[data-testid="stChatMessage"] [data-testid="stCaptionContainer"] {{
    color: {ACCENT};
    font-weight: 600;
    text-transform: lowercase;
    margin-bottom: 0.1rem;
}}

/* ---- Metric readouts: plain key/value text, no card ---- */
div[data-testid="stMetric"] {{
    background: transparent;
    border: none;
    padding: 0;
}}
div[data-testid="stMetric"] [data-testid="stMetricValue"] {{
    color: {ACCENT};
}}
div[data-testid="stMetric"] [data-testid="stMetricLabel"] {{
    color: {MUTED};
}}

/* ---- Chat input: underline prompt, pinned to the bottom of the viewport ----
   Deliberately NOT touching position/left/width/margin here: Streamlit's own
   bottom-bar container already aligns itself with the main content column
   (accounting for the sidebar's current width). Overriding those properties
   previously caused the input to render shifted left of the chat column. */
[data-testid="stBottom"] {{
    background: {BG} !important;
    border-top: 1px solid rgba(217,119,87,0.15);
}}
[data-testid="stBottomBlockContainer"] {{
    padding-top: 0.6rem;
}}
[data-testid="stChatInput"] {{
    border-radius: 0;
    border: none !important;
    border-bottom: 1px solid rgba(217,119,87,0.4) !important;
    background: transparent !important;
}}
[data-testid="stChatInput"] textarea {{
    font-size: 0.95rem;
    color: {TEXT} !important;
    caret-color: {ACCENT};
}}

/* ---- Buttons: plain text, no border/box ---- */
.stButton button {{
    border: none;
    background: transparent;
    color: {MUTED};
    text-align: left;
    padding-left: 0;
}}
.stButton button:hover {{
    color: {ACCENT};
    background: transparent;
    text-decoration: underline;
}}

/* ---- Persona toggle: plain text options, no boxes ---- */
div[data-testid="stRadio"] > div {{ gap: 0.8rem; }}
div[data-testid="stRadio"] label {{
    border: none;
    background: transparent;
    padding: 0;
}}

/* ---- Text inputs / selects: underline only ---- */
[data-testid="stTextInput"] input, [data-testid="stSelectbox"] div[data-baseweb="select"] > div {{
    border: none !important;
    border-bottom: 1px solid rgba(217,119,87,0.3) !important;
    border-radius: 0 !important;
    background: transparent !important;
}}

/* ---- Code blocks / expanders: minimal, no border box ---- */
[data-testid="stExpander"] {{
    border: none !important;
    background: transparent;
}}

/* ---- Sidebar: same palette, separated from the chat pane by a divider ---- */
section[data-testid="stSidebar"] {{
    background-color: {BG};
    border-right: 1px solid rgba(217,119,87,0.2);
}}
section[data-testid="stSidebar"] hr {{ border-color: rgba(217,119,87,0.15); }}
</style>
"""


def inject():
    st.markdown(_CSS, unsafe_allow_html=True)
