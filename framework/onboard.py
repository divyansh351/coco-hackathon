"""
Onboarding orchestrator: chains Phase 1a (metadata extraction) -> Phase 1b
(ontology mapping) -> Phase 1c (template instantiation) -> Phase 1d
(deployment + validation gate) into a single CLI command.

Usage:
    python onboard.py --connection UU60334 \\
        --source-db SC_DEMO --source-schema RAW \\
        --target-db SC_DEMO --target-schema ANALYTICS_AUTO \\
        --view-name SUPPLY_CHAIN_ANALYTICS_AUTO

Writes all intermediate artifacts to framework/runs/<timestamp>/:
    manifest.json, mapping.json, mapping_report.md, generated_view.yaml,
    validation_report.md
"""
import argparse
import datetime
import json
import os

import sf_connect
import extract_metadata
import map_ontology
import instantiate_template as it

FRAMEWORK_DIR = os.path.dirname(__file__)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(args):
    run_dir = os.path.join(FRAMEWORK_DIR, "runs", datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(run_dir, exist_ok=True)

    conn = sf_connect.connect(args.connection)
    cur = conn.cursor()

    ontology = load_json(args.ontology)
    templates = load_json(args.metric_templates)
    query_templates = load_json(args.query_templates)

    # --- Phase 1a: metadata extraction --------------------------------
    print(f"[1a] Extracting metadata from {args.source_db}.{args.source_schema} ...")
    manifest = extract_metadata.extract(args.connection, args.source_db, args.source_schema)
    with open(os.path.join(run_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # --- Phase 1b: ontology mapping ------------------------------------
    print("[1b] Mapping source schema to ontology (3-pass, LLM-assisted) ...")
    mapping = map_ontology.run(cur, manifest, ontology)
    with open(os.path.join(run_dir, "mapping.json"), "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2)
    with open(os.path.join(run_dir, "mapping_report.md"), "w", encoding="utf-8") as f:
        f.write(map_ontology.render_mapping_report(mapping))

    if mapping["overall_status"] != "OK":
        print(f"[1b] REJECTED: cross-validation found blocking issues. See {run_dir}/mapping_report.md")
        cur.close()
        conn.close()
        return

    # --- Phase 1c: template instantiation ------------------------------
    print("[1c] Instantiating templates into a concrete semantic view YAML ...")
    result = it.run(cur, mapping, manifest, ontology, templates, query_templates,
                     args.view_name, args.target_db, args.target_schema)
    with open(os.path.join(run_dir, "generated_view.yaml"), "w", encoding="utf-8") as f:
        f.write(result["view_yaml"])

    # --- Phase 1d: deploy + validation gate ----------------------------
    print(f"[1d] Deploying to {args.target_db}.{args.target_schema} and running validation gate ...")
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {args.target_db}.{args.target_schema}")
    yaml_str = result["view_yaml"]
    cur.execute(f"CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('{args.target_db}.{args.target_schema}', $${yaml_str}$$)")
    print(cur.fetchone()[0])

    view_fqn = f"{args.target_db}.{args.target_schema}.{args.view_name}"
    cur.execute(f"SELECT * FROM SEMANTIC_VIEW({view_fqn} METRICS fill_rate, on_time_delivery_rate, "
                f"otif_rate, perfect_order_rate, supplier_defect_rate, avg_lead_time_days, order_cycle_time_days)")
    cols = [c[0] for c in cur.description]
    row = cur.fetchone()
    metrics_out = dict(zip(cols, row))

    checks = []
    checks.append(("fill_rate in [0,100]", 0 <= metrics_out.get("FILL_RATE", -1) <= 102))
    checks.append(("avg_lead_time_days positive and < 365", 0 < metrics_out.get("AVG_LEAD_TIME_DAYS", -1) < 365))
    checks.append(("perfect_order_rate <= otif_rate <= on_time_delivery_rate",
                   metrics_out.get("PERFECT_ORDER_RATE", 1e9) <= metrics_out.get("OTIF_RATE", -1) <= metrics_out.get("ON_TIME_DELIVERY_RATE", -1)))

    vq_results = []
    for vq in result["verified_queries"]:
        try:
            cur.execute(vq["sql"].replace(f"__{args.view_name.lower()}", view_fqn))
            rows = cur.fetchall()
            vq_results.append((vq["name"], "PASS" if rows else "FAIL (empty)"))
        except Exception as e:
            vq_results.append((vq["name"], f"FAIL ({e})"))

    report_lines = [
        "# Validation Report", "",
        f"View: `{view_fqn}`", "",
        "## Metric values", "",
        "| Metric | Value |", "|---|---|",
    ]
    for k, v in metrics_out.items():
        report_lines.append(f"| {k} | {v} |")
    report_lines += ["", "## Sanity-bound checks", "", "| Check | Result |", "|---|---|"]
    for name, passed in checks:
        report_lines.append(f"| {name} | {'PASS' if passed else 'FAIL'} |")
    report_lines += ["", "## Verified query round-trip", "", "| Query | Result |", "|---|---|"]
    for name, res in vq_results:
        report_lines.append(f"| {name} | {res} |")

    all_pass = all(p for _, p in checks) and all(r == "PASS" for _, r in vq_results)
    report_lines.insert(1, f"\n**Overall: {'PASS' if all_pass else 'NEEDS REVIEW'}**\n")

    with open(os.path.join(run_dir, "validation_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    cur.close()
    conn.close()
    print(f"\nDone. Artifacts written to {run_dir}")
    print(f"Overall validation: {'PASS' if all_pass else 'NEEDS REVIEW -- see validation_report.md'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", default="UU60334")
    parser.add_argument("--source-db", required=True)
    parser.add_argument("--source-schema", required=True)
    parser.add_argument("--target-db", required=True)
    parser.add_argument("--target-schema", required=True)
    parser.add_argument("--view-name", required=True)
    parser.add_argument("--ontology", default=os.path.join(FRAMEWORK_DIR, "ontology.json"))
    parser.add_argument("--metric-templates", default=os.path.join(FRAMEWORK_DIR, "metric_templates.json"))
    parser.add_argument("--query-templates", default=os.path.join(FRAMEWORK_DIR, "persona_query_templates.json"))
    main(parser.parse_args())
