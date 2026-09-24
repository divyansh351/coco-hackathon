"""
Connection helper for the Streamlit app.

Inside an SPCS container, Snowflake auto-injects an OAuth token at
/snowflake/session/token plus SNOWFLAKE_HOST/SNOWFLAKE_ACCOUNT env vars --
no login, no PAT, no browser. The container authenticates as the role that
owns the service (see service-spec.yaml / scripts/deploy_service.py).

Outside a container (local `streamlit run` for development), fall back to
the same PAT-based path framework/sf_connect.py uses.
"""
import os

import snowflake.connector

SPCS_TOKEN_PATH = "/snowflake/session/token"


def get_connection():
    if os.path.exists(SPCS_TOKEN_PATH):
        with open(SPCS_TOKEN_PATH, encoding="utf-8") as f:
            token = f.read()
        return snowflake.connector.connect(
            host=os.environ["SNOWFLAKE_HOST"],
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            token=token,
            authenticator="oauth",
            warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        )

    # Local dev fallback (outside SPCS): PAT via env var, same mechanism as
    # framework/sf_connect.py.
    connection_name = os.environ.get("SNOWFLAKE_CONNECTION", "UU60334_PAT")
    pat = os.environ.get("SNOWFLAKE_PAT")
    if pat:
        return snowflake.connector.connect(connection_name=connection_name, token=pat)
    return snowflake.connector.connect(connection_name=connection_name)
