# QI9-2604-A01 validation of Claude-authored pdf2kicad

Status: **BLOCKED — extractor has not yet been executed against this source PDF**.

## Source identity

- PDF: `-QI9-2604-A01- Module IO.pdf`
- SHA-256: `a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc`
- 14 pages; 1,592,322 bytes.
- Pages 5–11 contain dense vector geometry and outlined text; pages 12–14 contain raster images.
- On pages 5–11, PyMuPDF inspection found 5,583–13,999 drawing items per page, with 86.3–94.3% of line/shape items having short geometry (<=5 PDF points). The text layer has 35 words per page and only 2–3 reference-like tokens. The extractor's classifier therefore selects `strokefont` for the relevant schematic pages 6–8.

The source PDF SHA-256 matches the hash recorded in `schematic_reference_verification.json`.

## Compatibility finding

The README describes the extractor as experimental and primarily designed for vector PDFs plotted by KiCad, where text is encoded as KiCad `newstroke` polylines. Its "other PDF" path says Altium/OrCAD/Eagle-style text-layer exports use real PDF text. This source has outlined text rather than a useful text layer, so it enters the KiCad stroke-font decoder despite the drawing source not being established as KiCad.

This is a **compatibility risk**, not proof that decoding fails. The decoder and topology must be run against the exact PDF before claiming success.

## Source-code risks requiring test coverage

1. `topology.py` treats only horizontal/vertical `line` primitives as wires. Diagonal and curved geometry is classified as body/other geometry.
2. Wire connectivity is built from coincident endpoints and detected filled dots. Interior crossings are not generally split into graph vertices; correctness depends on source export segmentation and dot detection.
3. Body/pin attachment uses fixed PDF-point tolerances (`END_TOL`, `TOUCH`, `REACH`) and geometric heuristics. Their scale sensitivity must be measured on this PDF.
4. `netlist.py:nets()` assigns synthetic names `N<id>` to unlabeled wire components. These must never be treated as verified net names.
5. Pin numbers are associated by nearest-token heuristics. Coordinate fallback names are explicitly not verified pin identities.
6. The generated KiCad verifier checks generated structure/CSV consistency; it is not an independent reconstruction of electrical connectivity from the PDF.

## Ground-truth anchors for a smoke test

These six records are explicitly marked VERIFIED in the source verification JSON:

| Page | Ref | Pin | Net |
|---:|---|---:|---|
| 6 | U5 | 1 (-Vin) | GNDREF |
| 6 | U5 | 3 (-Vout) | GND_RS232 |
| 7 | U9 | 1 (-Vin) | GNDREF |
| 7 | U9 | 3 (-Vout) | GND_RS485 |
| 8 | J5 | 6 (RD-) | RXN |
| 8 | J5 | 3 (TD-) | TXN |

The tokens `U5-` and `U9-` remain UNRESOLVED because they do not identify a unique pin. The JSON explicitly says its ten records do not certify complete electrical connectivity or BOM completeness.

## Reproducible local execution

From `tools/pdf2kicad`:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pdf2kicad.cli "C:\path\to\-QI9-2604-A01- Module IO.pdf" 6 out\page6
python -m pdf2kicad.cli "C:\path\to\-QI9-2604-A01- Module IO.pdf" 7 out\page7
python -m pdf2kicad.cli "C:\path\to\-QI9-2604-A01- Module IO.pdf" 8 out\page8
```

Run the existing unit tests with `python -m pytest -q`. The PDF-dependent variant tests require `PDF2KICAD_TEST_PDF` and are not substitutes for the three-page smoke test above.

## Gate

- [x] Source PDF identity and page geometry inspected.
- [x] Existing verified pin/net anchors extracted from the verification JSON.
- [x] Compatibility risks identified from source code and PDF classification.
- [ ] Run extractor on pages 6, 7, and 8.
- [ ] Compare exact ref/pin/net rows with the six verified anchors.
- [ ] Inspect false positives, missing pins, junctions, and `N<id>` assignments.
- [ ] Record runtime and peak memory.
- [ ] Independent full G3/G4 topology verification.

**No extractor PASS, accuracy score, runtime benchmark, or G3/G4 completion is claimed by this report.**
