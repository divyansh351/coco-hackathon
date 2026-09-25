"""
Terminal-style visual theme for the Streamlit app: dark background, monospace
font, sharp corners, green terminal-readout accents. Kept separate from
app.py so page wiring and visual styling can change independently.
"""
import streamlit as st

TERMINAL_GREEN = "#39ff88"

_CSS = f"""
<style>
/* ---- App chrome: hide Streamlit boilerplate ---- */
#MainMenu, footer, [data-testid="stDecoration"] {{ visibility: hidden; }}
header[data-testid="stHeader"] {{ background: transparent; }}

/* ---- Terminal aesthetic: monospace everywhere, dark, sharp corners ---- */
html, body, [class*="css"], input, textarea, button {{
    font-family: "Cascadia Code", "JetBrains Mono", "Fira Code", Consolas, "Courier New", monospace !important;
}}

.stApp {{ background-color: #0a0e0c; }}

.block-container {{
    padding-top: 1.5rem;
    padding-bottom: 6rem;
    max-width: 860px;
    margin: 0 auto;
}}

h1, h2, h3 {{ color: {TERMINAL_GREEN}; font-weight: 600; text-shadow: 0 0 6px rgba(57,255,136,0.25); }}
h1 {{ font-size: 1.4rem; }}

.term-titlebar {{
    border: 1px solid rgba(57,255,136,0.35);
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    padding: 0.4rem 0.9rem;
    font-size: 0.8rem;
    color: rgba(201,247,216,0.6);
    background: rgba(57,255,136,0.05);
    display: flex;
    gap: 0.4rem;
    align-items: center;
}}
.term-dot {{ width: 9px; height: 9px; border-radius: 50%; display: inline-block; }}

.stTabs [data-baseweb="tab-list"] {{ gap: 1.5rem; justify-content: center; border-bottom: 1px solid rgba(57,255,136,0.2); }}
.stTabs [data-baseweb="tab"] {{ font-weight: 550; color: rgba(201,247,216,0.7); }}
.stTabs [aria-selected="true"] {{ color: {TERMINAL_GREEN} !important; }}

/* ---- Chat messages styled as terminal blocks ---- */
[data-testid="stChatMessage"] {{
    padding: 0.6rem 0.9rem;
    margin-bottom: 0.9rem;
    border: 1px solid rgba(57,255,136,0.18);
    border-radius: 4px;
    background: rgba(57,255,136,0.03);
    gap: 0.6rem;
}}
[data-testid="stChatMessageAvatarUser"],
[data-testid="stChatMessageAvatarAssistant"] {{
    width: 26px;
    height: 26px;
    border-radius: 3px !important;
    background: transparent !important;
    font-size: 0.9rem;
}}
[data-testid="stChatMessageContent"] p {{
    margin-bottom: 0.5rem;
    line-height: 1.5;
    color: #c9f7d8;
}}
[data-testid="stChatMessageContent"] code {{
    color: {TERMINAL_GREEN};
    background: rgba(57,255,136,0.08);
}}

/* user turn: prompt-like, slightly brighter border */
div[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {{
    border-color: rgba(57,255,136,0.4);
    background: rgba(57,255,136,0.06);
}}

/* ---- Metric cards: terminal readout style ---- */
div[data-testid="stMetric"] {{
    background: #0d1310;
    border: 1px solid rgba(57,255,136,0.35);
    border-radius: 4px;
    padding: 0.9rem 1.1rem;
}}
div[data-testid="stMetric"] [data-testid="stMetricValue"] {{
    color: {TERMINAL_GREEN};
    text-shadow: 0 0 8px rgba(57,255,136,0.35);
}}

/* ---- Chat input styled like a shell prompt ---- */
[data-testid="stChatInput"] {{
    border-radius: 4px;
    border: 1px solid rgba(57,255,136,0.4) !important;
    background: #0d1310 !important;
    box-shadow: 0 0 12px rgba(57,255,136,0.06);
}}
[data-testid="stChatInput"] textarea {{
    font-size: 0.95rem;
    color: {TERMINAL_GREEN} !important;
    caret-color: {TERMINAL_GREEN};
}}

/* ---- Buttons: sharp, bracketed, terminal-command look ---- */
.stButton button {{
    border-radius: 3px;
    border: 1px solid rgba(57,255,136,0.35);
    background: rgba(57,255,136,0.04);
    color: {TERMINAL_GREEN};
}}
.stButton button:hover {{
    border-color: {TERMINAL_GREEN};
    background: rgba(57,255,136,0.12);
}}

/* ---- Code blocks / expanders ---- */
[data-testid="stExpander"] {{
    border: 1px solid rgba(57,255,136,0.2) !important;
    border-radius: 4px !important;
    background: rgba(57,255,136,0.02);
}}
pre, code {{ border-radius: 3px !important; }}

/* ---- Dataframes ---- */
[data-testid="stDataFrame"] {{ border: 1px solid rgba(57,255,136,0.2); border-radius: 4px; }}

/* ---- Sidebar ---- */
section[data-testid="stSidebar"] {{
    border-right: 1px solid rgba(57,255,136,0.2);
    background-color: #0a0e0c;
}}
</style>
"""


def inject():
    st.markdown(_CSS, unsafe_allow_html=True)
