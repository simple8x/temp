# pdf2kicad (experimental)

Extracts schematic structure from **vector PDFs plotted by KiCad** (no text layer: text is stroke-font polylines).
Pipeline: vector segments -> KiCad "newstroke" glyph template matching (text) -> wire graph / pin terminals -> ref & net assignment.

Install: `pip install -r requirements.txt` (pymupdf, numpy), then `python -m pdf2kicad ...` from this folder (or `pip install .` -> `pdf2kicad ...`).
Font data: `data/newstroke_ascii.json` is included for convenience (glyph outlines of KiCad's stroke font). Check KiCad's font licence for your use;
`python tools/fetch_font.py` rebuilds it from the KiCad source if you prefer to fetch it yourself.
Run: `python -m pdf2kicad.cli in.pdf PAGE out.csv`  (net, ref, pin-coordinate)

## Measured on 7 hand-verified pages (Module IO)
| page | refs found | ref-pair precision | recall |
|---|---|---|---|
| 5 | 37/37 | 0.97 | 0.97 |
| 6 | 27/27 | 0.98 | 0.95 |
| 7 | 27/27 | 0.95 | 0.85 |
| 8 | 15/15 | 1.00 | 0.92 |
| 9 | 10/10 | 1.00 | 0.92 |
| 10 | 45/45 | 0.99 | 0.90 |
| 11 | 48/48 | 0.99 | 0.92 |
All 209 references found and matching the BOM; 205/209 values identical to the hand-read ones (misses: J2 and R12, RN1, RN2 values).

## Known limits
- No part is missing on the 7 test pages. Multi-unit parts are merged (U16A..D -> U16).
- 7-30% of pins keep coordinate names (e.g. `401_135`) where no pin number is printed beside the stub (optocoupler units); nets are still right, rename by hand.
- Net names come from power symbols, label flags and labels at wire ends; unlabeled nets are `N<id>`.
- Symbol type inferred for R/L/F/C/CP/D/LED; ICs and connectors become generated boxes with pin names from inside the body.
- Only KiCad-origin vector PDFs. Not opened in real KiCad. Draft output: run tools/verify_kicad_sch.py and review against the drawing.

Usage: `python -m pdf2kicad.cli in.pdf PAGE outdir` then `python tools/gen_kicad_sch.py outdir/netlist.csv out.kicad_sch --parts outdir/parts.csv --pins outdir/pins.csv`


## Other PDFs (normalisation front end)
`python -m pdf2kicad.cli in.pdf PAGE outdir` first classifies the page (`--info` prints only that):

| kind | what it is | handling |
|---|---|---|
| strokefont | vector drawing, text as tiny stroke polylines (KiCad plot) | KiCad-font glyph decoder; tries other scales and 90/270 rotation if <98% of words decode |
| textlayer | vector drawing + real text (Altium/OrCAD/Eagle/LibreOffice-style exports) | uses the text layer; drawing scale estimated from the 100-mil grid (7.2pt) of wire lengths |
| raster | scanned / image only | **not extractable**: exits with code 3 and a message. Use OCR/vision, then feed the result to `tools/gen_kicad_sch.py` |

Normalised: PDF `/Rotate` flag ignored (stored coordinates used), sideways-stored content (`--rotate 90|180|270` or auto for stroke fonts),
drawing scale (`--scale` or auto), duplicate overdrawn lines, sheet frame / title-block strip. Verified on synthetic re-encodings of one
KiCad page (half/double size, sideways, text-layer version, raster) -> identical result (tests/test_variants.py).
**Not verified on real PDFs from other CAD tools**: only re-encodings of the KiCad page were available. Expect symbol-shape rules
(R/C/D/LED/IC) to need tuning for other libraries' symbols.

Whole project: `python tools/make_project.py OUT SRC` where SRC holds `pageNN/` folders produced by the CLI (hierarchical project, one sheet per page).

## Performance (page of ~10k segments, single core)
~1.3 s per KiCad page; all 7 test pages 11 s (was 76 s). Odd files (other scale / sideways) ~6 s (was 35 s): scale and
orientation are scored on a central window and the page is decoded once. Hot spots fixed: template matching uses squared
distances on size-compatible templates only; glyph linking skips pairs whose boxes do not touch and drops duplicate strokes;
orientation check reuses its decoded words; no scipy dependency (own Hungarian solver in `assign.py`).
