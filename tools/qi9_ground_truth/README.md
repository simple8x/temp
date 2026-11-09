# QI9 Ground Truth Authoring & Review Pilot

This package is independent from the PDF parser and topology engine. It supports
human-authored annotations and validates their evidence/review structure. It
does not infer symbol ownership, pin identity, wire roles, junction semantics,
or electrical net membership.

## Source identity

The QI9 source PDF is pinned as SHA-256:

`a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc`

Each page annotation export carries this exact digest. Coordinates are page-local
PDF points with a top-left origin. Rendered images are converted back to PDF
points using the render DPI and page geometry recorded in each review template.

## Build a review pack

From repository root, with the existing `pdf2kicad` dependencies installed:

```bash
python tools/pdf2kicad/native_symbol_review_pack.py \
  --pdf "/path/to/QI9-2604-A01 Module IO.pdf" \
  --pages 6 \
  --dpi 150 \
  --outdir ./qi9-review-pack
```

The pack contains:
- `page_06_source.png`: clean render of the original PDF (default blind-review surface).
- `page_06_candidate_overlay.png`: optional parser candidate overlay; hidden by default.
- `page_06_review_template.json`: source hash, page geometry, and candidate inventory.
- `manual_review_index.json`: pack manifest.

The clean source render is captured before candidate overlays are drawn. The app
does not display parser candidates unless the reviewer explicitly enables them.

## Open the offline review workspace

Open `tools/qi9_ground_truth/review_app.html` in a modern browser (Chrome/Edge
recommended). Select the generated review-pack folder using the folder picker.
No server, external library, account, or network request is required.

1. Keep **Show parser candidates** unchecked for blind first-pass review.
2. Select annotation kind: symbol boundary, pin identity, wire segment, junction,
   net label, or connectivity.
3. Choose rectangle, point, or line geometry and draw on the PDF image.
4. Enter a stable reviewer ID, decision, and evidence note; add the annotation.
5. A second distinct reviewer selects the annotation and uses **Add review**.
6. Two distinct unanimous CONFIRM decisions plus evidence are required before a
   non-connectivity annotation becomes `VERIFIED`. Connectivity becomes
   `VERIFIED_CONNECTED` or `VERIFIED_SEPARATE` only with explicit endpoints.
7. Export the page JSON or all reviewed pages. Drafts are also saved in browser
   local storage under the source hash and page.

The optional candidate list is collapsed by default. Selecting a candidate only
copies its reference/candidate geometry into the draft; it does not create a
verified label. The clean PDF render remains the default display.

## Validate exported annotations

For a single page:

```bash
python tools/qi9_ground_truth/validate_annotations.py page06.annotations.json \
  --expected-source-sha256 a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc
```

A successful result is only `VALID_ANNOTATION_STRUCTURE_ONLY`. It does not
certify label correctness, completeness, or electrical sign-off.

The all-pages workspace export is a review bundle containing per-page
annotation documents. Split its `pages[]` entries into page JSON files before
running the current single-page validator.

## Evidence and status policy

- Parser candidates and rankings are not ground truth.
- A confirmed annotation requires source evidence and two distinct reviewers
  who both chose CONFIRM; mixed decisions cannot pass. Reviewer IDs are self-declared labels, not authenticated identities; the project must arrange genuinely independent human review.
- Connectivity assertions require two existing annotation endpoints and a
  matching `CONNECTED_TO` or `SEPARATE_FROM` predicate.
- Disagreements remain `CONFLICT`; insufficient evidence remains `UNKNOWN`.
- Synthetic tests validate the annotation machinery only, not QI9 electrical truth.
- The existing page-6 symbol-boundary sample is not silently upgraded into
  this reviewed corpus.
