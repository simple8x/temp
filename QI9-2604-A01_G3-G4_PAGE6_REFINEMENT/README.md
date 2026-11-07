# QI9-2604-A01 — PDF schematic extraction test pack

## Purpose
A reproducible, evidence-preserving extraction pipeline for the supplied 14-page Module IO PDF. It prepares native PDF evidence and OCR text candidates for independent review. It does **not** claim a completed PDF-to-KiCad conversion.

## Current gate result

| Gate | Result | Evidence / reason |
|---|---|---|
| G0 — source intake | PASS | 14/14 pages; source SHA-256 recorded; PyMuPDF extraction manifest included |
| G1 — vector geometry | PASS | 7/7 schematic sheets (PDF pages 5–11); 71,935 native vector drawing primitives exported to JSON |
| G2 — text candidates | PASS (candidate extraction only) | Tesseract TSV+JSON on 7/7 sheets; 876 OCR word candidates; page 6 PNG included for visual spot-check |
| G3 — symbol/pin anchors | BLOCKED | No complete validated symbol-template library or reviewed pin-anchor dataset in this pack |
| G4 — electrical topology/netlist | BLOCKED | No verified crossing/junction/label-to-wire/pin topology; no netlist is generated |
| G5 — KiCad export/validation | BLOCKED | No `.kicad_sch` generated; `kicad-cli` was not available in the execution environment for parse/ERC validation |

**Overall: BLOCKED_PENDING_G3_G4_G5.** No `.kicad_pcb` is produced. Source PDF is never modified.

## Reproduce
Requirements: Python 3.10+, `pymupdf`; for OCR also Tesseract executable in PATH.

```bash
python pdf_only_intake.py "QI9-2604-A01 Module IO.pdf" --out out/g0
python vector_schematic_pipeline.py "QI9-2604-A01 Module IO.pdf" --out out/extract --dpi 200
python -m unittest -v test_topology_rules.py
```

The vector extractor processes PDF pages 5–11. It preserves primitive coordinates, item type, drawing index, color/fill and stroke width. OCR word boxes are in rendered-pixel coordinates and are **candidates only**; do not treat OCR adjacency as electrical attachment.

## Non-negotiable inference rules
- A geometric crossing is not an electrical junction unless explicit junction evidence or a verified shared endpoint exists.
- A label is not attached to a wire merely because it is nearby.
- A pin identity/anchor requires a validated symbol template or manual source review.
- Unknown or ambiguous cases remain `UNRESOLVED`; never force a net assignment.
- Only verified topology may enter a generated KiCad netlist/schematic.

`topology_rules.py` contains conservative geometric predicates, not a full connectivity engine. The seven tests validate those predicates only; they do not certify the PDF's circuit connectivity.

## Independent test protocol
Use `INDEPENDENT_RETEST_PROMPT.md` with the `source/` PDF and `pipeline/` code. Do not provide `reference_holdout/` to the independent model until its blind extraction report is frozen. Then compare disagreements against the holdout and the original PDF. The holdout file is a prior 10-record reference-resolution artifact; its own stated scope is limited to those records and it is not a complete netlist ground truth.

## Important limitations
- The PDF schematic labels are predominantly vector outlines; PDF native text extraction alone is insufficient.
- OCR can misread symbols, polarity, pin numbers and closely spaced text.
- Primitive extraction is lossless-ish evidence serialization, not semantic recognition.
- The current graph predicates do not handle all line-on-line overlaps, curved wires, diagonal junctions, multi-page net labels, symbol transforms or electrical rules.
- No KiCad file, ERC result, connectivity PASS, or release approval is asserted.
