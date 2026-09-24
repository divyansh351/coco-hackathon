# Reference: Ontology, Templates, and Correctness Rules

Detailed reference for `framework/ontology.json`, `framework/metric_templates.json`,
and `framework/persona_query_templates.json`, plus the correctness rules
`instantiate_template.py` already encodes. Read this before assuming a pipeline
failure is a new bug -- check whether it's one of the three known failure
modes below first.

## `ontology.json` shape

```json
{
  "entities": [
    {
      "name": "Demand",
      "attributes": [
        {"name": "demand_id", "type_category": "text", "required": true,
         "is_primary_key": true, "synonyms": ["order_id", "po_id"]},
        {"name": "order_date", "type_category": "date", "required": true,
         "synonyms": ["created_at"]}
      ],
      "foreign_keys": [
        {"references_entity": "Customer", "required": true},
        {"references_entity": "Facility", "required": true}
      ]
    }
  ]
}
```

- `type_category` is one of `numeric`, `date`, `text`, `other` -- used for
  compatible-type matching in both FK inference and attribute scoring.
- `foreign_keys` on an entity is the ONLY source of truth for which
  relationships the generated view will contain. `instantiate_template.py`'s
  `resolve_relationship_candidates()` builds relationships strictly from these
  declarations matched against inferred/declared FKs in the manifest -- it
  deliberately ignores any other FK the extraction phase happens to find
  between two mapped tables. Adding a relationship to the ontology (not just
  to the physical schema) is required before the engine will use it.

## `metric_templates.json` shape

Each metric has a `kind`:

- `"simple"` -- a single aggregate expression on one entity's facts, e.g.
  `SUM(shipped_qty)`. Becomes a table-scoped metric.
- `"derived"` -- combines metrics from two *different* entities across a
  non-1:1 relationship (e.g. fill_rate = shipped/ordered across
  Transaction<->DemandLine). MUST become a view-level top-level `metrics:`
  entry, never a table-scoped metric referencing another table -- referencing
  cross-table causes silent fan-out (duplicated/inflated aggregates) rather
  than an error, which is why this is encoded structurally via `kind` rather
  than left to string-parsing.
- `"nested_aggregate"` -- an aggregate over a per-row ratio/expression.

Facts can declare `cross_refs`: `{"entity": ..., "attribute": ...}` when their
expression references a column that lives on another entity's table. The
engine auto-generates a private "passthrough fact" on the referenced table if
that attribute isn't already exposed there as a dimension/time_dimension/fact
-- otherwise Snowflake raises `invalid identifier` because the column has no
standalone row-level declaration for the view to resolve.

## `persona_query_templates.json` shape

Each entry: `question`, `metrics` (list), optional `dimension_entity` /
`dimension_attribute` / `order_by`. An entry can set
`"requires_generated_month_dimension": true` -- these are currently skipped by
the resolver (see Known limitations in SKILL.md).

## Three correctness rules already handled automatically

1. **Fan-out avoidance** -- cross-entity metrics must be view-level derived
   metrics (see `kind: "derived"` above). Handled by `resolve_metrics()`
   splitting metrics into `by_table` (simple) vs. a top-level `derived` list.

2. **Denormalization detection** -- when a direct FK edge between two tables
   also has an alternate transitive path to the same target, a sampled-row SQL
   check (`COUNT(*) WHERE direct_value != transitive_value`) determines whether
   the direct edge is just a stored copy of the transitive value (0 mismatches
   -> denormalized, drop the direct edge) or a genuinely independent
   relationship (nonzero mismatches -> keep it, subject to rule 3).

3. **Ontology-scoped relationships (multi-path avoidance)** -- Snowflake
   rejects ANY query mixing a dimension/metric across two logical tables that
   have more than one relationship path between them, even when both paths are
   legitimate and independent (not denormalized copies). The fix is
   structural, not a special case: only build relationships from
   `ontology.json`'s declared `foreign_keys`, never from every incidental FK
   the extraction phase discovers between two ontology-mapped tables. See
   `implementation-findings.md` "Finding 3" for the full incident writeup
   (the `PARTS.PRIMARY_SUPPLIER_ID -> SUPPLIERS` case).

For the full incident history and the blind end-to-end validation run against
`SC_DEMO.RAW`, see `implementation-findings.md` and
`framework/runs/validation_report.md`.
