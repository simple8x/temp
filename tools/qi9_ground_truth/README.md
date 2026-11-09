# QI9 Ground Truth Authoring Pilot

This package is deliberately separate from the PDF parser and topology engine.
It validates human-authored annotations; it does not infer symbol ownership,
pin identity, wire roles, junction semantics, or net membership.

## Source identity

The current QI9 source PDF is pinned in the existing project audit as:

`a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc`

Every annotation page must carry the exact source SHA-256. A mismatch blocks
validation. Coordinates use PDF points with a top-left origin and are page-local.

## Annotation workflow

1. Generate candidate overlays and blank templates with the existing
   `native_symbol_review_pack.py`; candidate rectangles are not labels.
2. A reviewer records an annotation in the schema in
   `schemas/annotation.schema.json`, retaining PDF object IDs and page-local
   geometry.
3. A second independent reviewer confirms or disputes the same fact.
4. Run the validator. Only two distinct confirming reviewer IDs plus source
   evidence can produce a `VERIFIED*` status.
5. Disagreements remain `CONFLICT`; insufficient evidence remains `UNKNOWN`.
6. Freeze accepted annotation files by source hash and commit SHA before
   running any parser comparison.

## Validate

From repository root:

```bash
python tools/qi9_ground_truth/validate_annotations.py path/to/page06.annotations.json \
  --expected-source-sha256 a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc
```

The success status is only `VALID_ANNOTATION_STRUCTURE_ONLY`. It is not an
electrical sign-off and does not certify completeness or correctness of the
human labels.

## Important boundary

- Parser outputs and candidate rankings must not be imported as ground truth.
- `VERIFIED_CONNECTED` and `VERIFIED_SEPARATE` require explicit annotation
  endpoints, matching predicates, PDF evidence, and two independent reviewers.
- `UNKNOWN` and `CONFLICT` are valid terminal review outcomes.
- Synthetic fixtures test the validator only; they are not QI9 electrical truth.
- The existing page-6 symbol-boundary oracle is a legacy small-sample artifact;
  it is not silently upgraded into this two-reviewer corpus.
