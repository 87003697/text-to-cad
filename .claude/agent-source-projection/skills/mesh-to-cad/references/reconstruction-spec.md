# Reconstruction Spec

Use this document by default for mesh-to-cad executions. The Reconstruction
Spec is enabled by default; a task or pilot instruction may explicitly opt out
for a controlled execution. Do not add a CLI mode, an experiment field, or
automatic detection for it.

## Lifecycle

When enabled (the default unless the task or pilot explicitly opts out):

1. Inspect the raw input with `$mesh-inspect` and prepare the Canonical
   Reference with `$mesh-compare` using the normal workflow.
2. Before the first CAD authoring or Step 0, create
   `<EXP_DIR>/run/reconstruction-spec.json`.
3. Read it before initial modeling. Before every Repair Hypothesis, read it
   again. If the geometric understanding changes, update this same file in
   place.

The file is a mutable working document. Keep it under `run/`; it is not
Workspace authority and is not part of `setup/`, `steps/`, `cycles/`, or final
authority/Final Delivery. It does not change cadgen/runtime or the formal
Workspace schema state machine. Do not add revisions, digests, history, or
request records.

## Minimum JSON

The top level has exactly these three arrays: `components`, `features`, and
`relations`. They may be empty. A Component or Feature has an `id`. A Relation
has `id`, `kind`, `from`, and `to`:

```json
{
  "components": [
    {
      "id": "component.body",
      "description": "Main observed mass",
      "certainty": "observed",
      "evidence": "raw mesh inspection and Canonical Reference"
    }
  ],
  "features": [
    {"id": "feature.opening", "certainty": "inferred"}
  ],
  "relations": [
    {
      "id": "relation.opening-part-of-body",
      "kind": "part_of",
      "from": "feature.opening",
      "to": "component.body"
    }
  ]
}
```

`description`, `certainty`, and `evidence` are optional on any item. If
present, `certainty` is one of `observed`, `inferred`, `hidden`, `uncertain`,
or `mixed`; it is descriptive only and does not control validation, repair,
acceptance, or any other workflow behavior. Every Component, Feature, and
Relation ID is globally unique within one Spec, remains stable while it means
the same item, and every relation endpoint must name an existing Component or
Feature ID. Do not add `parent_id`: `part_of` is the only parent expression.

## Meaning

`part_of`, `depends_on`, and `affects` are reserved Organizational Relations.
Every Relation `kind` is non-empty; any other non-empty kind is an open
Constructive Relation. Do not create a relation registry or kind-specific
endpoint/cardinality ontology.

The Spec records reference semantics hypothesized from raw-mesh inspection and
Canonical Reference geometry. At creation, derive items only from those
geometric evidence sources. Ignore user-provided category, function, and
part-semantic hints. It is not a CAD plan or source plan: Repair Targets, source
callables, AssemblyHelper objects, and STEP labels do not automatically become
Spec items. IDs remain independent of those implementation identities.

When useful, cite a Spec ID in prose Repair Hypotheses, plans, or notes. Do not
add a Spec field to Repair Batch or other Workspace output schemas.
