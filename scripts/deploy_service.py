"""
Idempotent SPCS service deploy, run from GitHub Actions after the image is
pushed. Authenticates with a PAT (no browser, no interactive login -- see
implementation-findings.md Finding 4) and creates or updates the service
under SUPPLY_CHAIN_APP_ROLE so the running container inherits that role's
(scoped, not ACCOUNTADMIN) privileges.

Env vars required: SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PAT
"""
import os

import snowflake.connector

ACCOUNT = os.environ["SNOWFLAKE_ACCOUNT"]
USER = os.environ["SNOWFLAKE_USER"]
PAT = os.environ["SNOWFLAKE_PAT"]

SERVICE_NAME = "SC_DEMO.APP.SUPPLY_CHAIN_APP"
COMPUTE_POOL = "SUPPLY_CHAIN_APP_POOL"
SPEC_PATH = os.path.join(os.path.dirname(__file__), "..", "service-spec.yaml")


def main():
    conn = snowflake.connector.connect(
        account=ACCOUNT,
        user=USER,
        token=PAT,
        authenticator="PROGRAMMATIC_ACCESS_TOKEN",
        role="ACCOUNTADMIN",
    )
    cur = conn.cursor()
    cur.execute("USE ROLE SUPPLY_CHAIN_APP_ROLE")

    with open(SPEC_PATH, encoding="utf-8") as f:
        spec = f.read()

    cur.execute(f"SHOW SERVICES LIKE 'SUPPLY_CHAIN_APP' IN SCHEMA SC_DEMO.APP")
    exists = cur.fetchall()

    if exists:
        print(f"Service {SERVICE_NAME} exists -- updating via ALTER SERVICE ...")
        cur.execute(f"ALTER SERVICE {SERVICE_NAME} FROM SPECIFICATION $$\n{spec}\n$$")
    else:
        print(f"Creating service {SERVICE_NAME} ...")
        cur.execute(
            f"CREATE SERVICE {SERVICE_NAME} IN COMPUTE POOL {COMPUTE_POOL} "
            f"FROM SPECIFICATION $$\n{spec}\n$$ MIN_INSTANCES=1 MAX_INSTANCES=1"
        )
    print(cur.fetchone()[0])

    cur.execute(f"SHOW ENDPOINTS IN SERVICE {SERVICE_NAME}")
    cols = [c[0] for c in cur.description]
    for row in cur.fetchall():
        d = dict(zip(cols, row))
        print(f"Endpoint {d.get('name')}: {d.get('ingress_url')}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
