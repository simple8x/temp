# QI9-2604-A01 — G3/G4 execution status

## Scope and evidence
- Source PDF SHA-256: `a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc`.
- G3 scanned OCR candidates from source schematic pages 5–11. It emitted reference-designator candidates and net-label text candidates with original page, OCR confidence, and pixel bounding box.
- G3 preserves six unique pin-to-net facts from the supplied source-reviewed reference-resolution artifact. These are reference-resolution evidence only, not a complete symbol library or complete pin map.
- G4 extracted PDF line primitives and exact shared-endpoint candidates. The primitive population includes wires, symbol outlines, text glyph strokes and drawing frames; they are NOT classified as wires. Therefore these are geometric candidates, not a proven electrical graph.

## Results
- 58 OCR reference-designator candidates.
- 63 OCR net-label candidates.
- 6 unique reviewed pin-to-net facts (J5 RD-/RXN; J5 TD-/TXN; U5 -Vin/GNDREF; U5 -Vout/GND_RS232; U9 -Vin/GNDREF; U9 -Vout/GND_RS485).
- 70,278 line primitives; 50,271 exact shared-endpoint geometric candidates.
- 0 generated net-membership claims; no netlist generated.
- G3/G4 tests: 6/6 PASS.

## Gate decision
G3 = PARTIAL / BLOCKED. OCR reference candidates are not enough to establish component symbols, pin numbering, or anchor coordinates. Only six pre-reviewed facts are retained, and unresolved references remain unresolved.

G4 = PARTIAL GEOMETRY ONLY / BLOCKED. No reliable wire-vs-symbol-vs-text classifier, junction-dot classifier, crossing semantics, label attachment, or multi-page label scope has been validated. The pipeline deliberately does not infer net connectivity.

G5 remains BLOCKED. No `.kicad_sch`, netlist, or ERC result is produced. `kicad-cli` is not available in the execution environment.

## Reproduction
Run from package root:

```bash
python g3_g4_pipeline.py
python -m unittest -v test_g3_g4.py
```

No source PDF mutation is performed.


## Algorithm repair branch — 2026-11-05
- Added `g3_g4_geometry.py` with explicit endpoint/T/crossing/overlap/degenerate classification and fail-closed electrical evidence gates.
- Added `test_g3_g4_geometry.py`: 14 local regression tests PASS.
- The repair is a reusable geometry/evidence layer; existing extracted primitives still have no independently reviewed wire-role labels or junction-dot records. Therefore this patch does not convert the existing 70,278 primitives into verified wires and does not change G3/G4 release status.
- G3/G4 remain BLOCKED for full electrical topology and netlist generation until the actual PDF page evidence is annotated and fed into the classifier.
- No KiCad netlist generated; no source data changed.
