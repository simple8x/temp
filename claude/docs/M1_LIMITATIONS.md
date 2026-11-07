# M1 limitations and unsupported cases (honest list)

All items are **FACT** about the current code unless marked ASSUMPTION. Each is also reported
at runtime in `IngestReport` (nothing is dropped silently).

## Ingestion
- Clipping paths (`W`/`W*`) are **not applied**; geometry hidden by a clip is still emitted. Form
  XObject `/BBox` clipping is likewise not applied. Reported as `clip_not_applied`,
  `form_bbox_clip_not_applied`.
- Optional content (layers, `/OC`) is **not filtered**: hidden layers are emitted. Reported as
  `optional_content_not_filtered`. Real schematics often rely on layers; ASSUMPTION: this matters.
- Text, images, inline images, shadings and patterns are not interpreted (reported).
- Soft masks, blend modes, transparency and all ExtGState keys except `/LW` and `/D` are ignored.
- Stroke width under an anisotropic CTM is approximate (flagged per primitive).
- Type 3 fonts / glyph-as-path text is just paths; no glyph identification.
- Dash patterns are recorded but not expanded into segments.
- Colour is recorded as the raw operator values (no colour-space conversion, no ICC).
- Annotations and form-field appearance streams are not read (page content only).
- Encrypted PDFs need the password; no OCR/raster path in M1 (M6).

## Arrangement
- `eps` is a touch tolerance with **transitive chaining** (three endpoints each 0.9·eps apart
  become one node). Chosen eps must be justified per document (ASSUMPTION: use ~0.25 × the
  median stroke width; not validated on real schematics).
- Arcs are polylines; no analytic arc/line intersections.
- Worst case O(n²) pairs; guarded by `max_pairs` (raises `PAIR_LIMIT`).
- Filled shapes contribute only their outline as line geometry; area semantics are not modelled.
- No junction-dot detection, no role/line classification (M2), no connectivity (M3).

## Validation scope
- Property tests use small integer lattices and eps ≪ spacing; they show agreement with an exact
  oracle in that regime, not behaviour at tolerance boundaries on real data.
- The PyMuPDF cross-check covers generated pages (lines, polylines, Béziers, rects, closed paths,
  `cm`, page rotation, MediaBox offset). It does **not** cover forms, CropBox, UserUnit or real
  schematic PDFs. Agreement of two extractors is not proof of correctness.
- **No real schematic PDF has been run through M1.** The repository's own input PDF has not been
  used by M1 yet (see M1 status for whether a smoke run was done).
