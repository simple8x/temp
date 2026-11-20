# M4.0 status: independent ground truth from KiCad (FACT / INFERRED / ASSUMPTION marked)

## Method (FACT)
- Ground truth = KiCad 9.0.9 `sch export pdf` + `export netlist --format kicadxml` on the demos of
  `KiCad/kicad-source-mirror` (SHA 8c85ac14529b4375d75f2bb3d0e97e6145b0a32c). ECCI sees only the PDF.
- Pin positions come from the `.kicad_sch` (embedded lib_symbols). Placement order (mirror/rotate) is decided
  per instance from the schematic's own wire ends; undecidable instances and duplicated (ref, pin) are excluded.
- Oracle level: GT pin positions attached to wire ends (<= 0.6 pt). Detected level: pins found by ECCI (<= 1.0 pt).
- Scope: 19 single-page projects. 4 export failures, hierarchical / multi-page projects are skipped.

## Changes made because of the measurements
- Wire style by structure, never by count; styles of tiny total length, tiny mean length, page-spanning rules
  (sheet furniture) and colours that also draw closed outlines are not wire candidates; length breaks a structural
  tie (ASSUMPTION); remaining ambiguity => no wires at all (UNKNOWN), never a guess.
- Text: second, row-independent kind of glyph evidence (component of >= 4 prims, >= 80% tiny 2-point strokes).
- Clustering: dangling axis-aligned stubs (pin lines drawn in body colour; contact uses stroke width) are set aside
  before proximity merging and re-attached afterwards; parts smaller than 0.3% of the page diagonal merge only by
  touching. Template matching retries without stubs; the stub primitive is the independent second evidence (S2).
- GT reader: per-instance placement order.

## Results: see the claude-gt run of the commit named in the report (GT_TOTAL line). Numbers are NOT repeated here.

## Known limits (FACT)
- Connections made only through net labels / power symbols / buses are not recovered (no OCR names): recall limit.
- generic_opamp_bip-type pages where two colours have equal structure may stay UNKNOWN by design.
- Hidden pins not scoreable; duplicated references excluded; thresholds marked ASSUMPTION are uncalibrated.
- Tests, mutation 78 entries (M61-M69 new) run in claude-m1; GT numbers come from KiCad output only.
