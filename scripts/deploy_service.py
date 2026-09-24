"""
Idempotent SPCS service deploy, run from GitHub Actions after the image is
pushed. Authenticates with a PAT that is role-restricted directly to
SUPPLY_CHAIN_APP_ROLE (no browser, no interactive login -- see
implementation-findings.md Finding 4). A role-restricted PAT session cannot
USE ROLE to switch (Snowflake blocks it: "Current session is restricted"),
so the token itself must already be scoped to the target role -- the service
this creates is then owned by that role, and the running container inherits
its (scoped, not ACCOUNTADMIN) privileges.

Env vars required: SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_APP_PAT
"""
import os

import snowflake.connector

ACCOUNT = os.environ["SNOWFLAKE_ACCOUNT"]
USER = os.environ["SNOWFLAKE_USER"]
PAT = os.environ["SNOWFLAKE_APP_PAT"]

SERVICE_NAME = "SC_DEMO.APP.SUPPLY_CHAIN_APP"
COMPUTE_POOL = "SUPPLY_CHAIN_APP_POOL"
SPEC_PATH = os.path.join(os.path.dirname(__file__), "..", "service-spec.yaml")


def main():
    print(f"snowflake-connector-python version: {snowflake.connector.__version__}")
    print(f"PAT length: {len(PAT)}")

    conn = snowflake.connector.connect(
        account=ACCOUNT,
        user=USER,
        password=PAT,
        token=PAT,
        authenticator="PROGRAMMATIC_ACCESS_TOKEN",
    )
    cur = conn.cursor()

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
