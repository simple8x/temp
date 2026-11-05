# QI9-2604-A01 — G3/G4 page-6 actual run

**Run decision: `BLOCKED_ELECTRICAL_TOPOLOGY`.** The actual source PDF page was processed. This does not establish a complete electrical graph and does not generate a KiCad netlist.

## Source and reproduction

- Source: `[QI9-2604-A01] Module IO.pdf`
- SHA-256: `a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc`
- PDF page index: 6 (footer label also shows page 6)
- Render: 2526 × 1786 at 3.0×
- The SHA-256 in `schematic_reference_verification.json` matches the source PDF.

Run from this directory:

```bash
python run_g3_g4_page6.py --pdf "/path/to/[QI9-2604-A01] Module IO.pdf" --reference "/path/to/schematic_reference_verification.json" --page 6 --output G3_G4_PAGE6_RUN_RESULT.json
python -m unittest -v test_g3_g4_geometry.py
```

Dependencies: PyMuPDF, pytesseract, Pillow, and Tesseract OCR.

## G3 — OCR candidates and reviewed facts

- OCR word occurrences: **190**
- Refdes-like candidate occurrences: **20** (20 unique tokens)
- Net-label-like candidate occurrences: **24** (7 unique tokens)
- Reviewed unique pin/net facts in the source-review artifact: **6** across the full source.
- Reviewed facts on page 6: **2**
  - U5 / B0505-1W, pin 1 `-Vin` → `GNDREF` (`REF-007`)
  - U5 / B0505-1W, pin 3 `-Vout` → `GND_RS232` (`REF-008`)

OCR tokens remain candidates. OCR does not establish symbol identity, pin anchors, or label attachment.

## G4 — page-6 vector geometry

- Raw PDF line primitives: **9,503**
- Spatial broad-phase candidate pairs: **91,378**
- Theoretical all-pairs comparisons without broad-phase pruning: **45,148,753**
- Broad-phase pruning: **99.7976%**
- Geometric relation candidates after exact relation checks: **16,993**
  - endpoint/endpoint: **13,410**
  - T contacts: **609**
  - crossings: **137**
  - collinear overlaps: **2,837**

These are geometric relations across all extracted strokes, including symbol outlines, text glyph strokes, and drawing frames. They are **not** wire or junction counts.

## Evidence gate

| Item | Result |
|---|---:|
| Segments with independently verified `WIRE` role | 0 |
| Verified junction-dot records supplied to geometry pass | 0 |
| Verified geometry joins | 0 |
| Net-membership claims | 0 |
| KiCad netlist generated | No |

Every extracted segment was passed as `role=UNKNOWN`. The algorithm therefore returns zero verified joins despite finding geometric contacts. No connectivity was inferred from proximity, crossings, or shared endpoints.

## Algorithm repair and tests

`g3_g4_geometry.py` now uses a page-scoped uniform-grid broad phase with a long-segment fallback. It retains exact relation checks and evidence gates; it only prunes pairs whose bounding boxes cannot meet. The fallback preserves candidate coverage for long bounding boxes.

Local regression suite: **19/19 PASS** (14 evidence/geometry tests + 5 spatial-index tests). The actual page-6 run completed in approximately **0.44 s** in this execution environment. This is a local run/test result, not a GitHub Actions CI result.

## Final gate

- G3: `PARTIAL_CANDIDATES_ONLY`
- G4: `GEOMETRY_ONLY_BLOCKED`
- Electrical topology / netlist: `BLOCKED`

Next required evidence is reviewed wire-role and junction-dot annotation for page 6, plus verified symbol pin-anchor templates. Until then, unreviewed topology remains `UNKNOWN`.
