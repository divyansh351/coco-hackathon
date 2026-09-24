"""
Shared connection helper for framework/*.py scripts.

Standalone Python scripts on this machine can't rely on browser-SSO token
caching: the standalone interpreter has no working OS secure-storage backend,
and even with `keyring` installed, Windows Credential Manager rejects tokens
this large (CredWrite error 1783). Use a Personal Access Token (PAT) instead
-- PAT auth involves no browser interaction at all.

Usage:
    import sf_connect
    conn = sf_connect.connect(args.connection)

If the SNOWFLAKE_PAT environment variable is set, it's passed to the connector
as the `token` parameter, overriding the named profile's own auth method (so
the same `--connection UU60334_PAT` profile in connections.toml never needs
the raw token written into it). Inject the env var via:
    cortex secret run --map "sf-scripts-pat=SNOWFLAKE_PAT" -- python <script> ...
"""
import os

import snowflake.connector


def connect(connection_name):
    pat = os.environ.get("SNOWFLAKE_PAT")
    if pat:
        # The connector expects the PAT via the `token` parameter, not `password`,
        # when authenticator="PROGRAMMATIC_ACCESS_TOKEN" (set in the connection
        # profile) -- passing it as `password` is silently rejected as "invalid".
        return snowflake.connector.connect(connection_name=connection_name, token=pat)
    return snowflake.connector.connect(connection_name=connection_name)
