# Independent AI retest instructions (blind pass)

You are an independent reviewer. Input: `source/QI9-2604-A01 Module IO.pdf` and the scripts/schema under `pipeline/`.

1. Verify source page count and SHA-256 independently.
2. Run G0 intake and G1 vector extraction. Compare page coverage, primitive counts and deterministic outputs. Do not copy prior result values without running.
3. Run G2 OCR at 200 DPI on source PDF pages 5–11. Report raw candidate text and bounding boxes. Visually inspect at least one sheet and flag OCR ambiguities.
4. Do not infer electrical connectivity from OCR proximity or line crossings alone. Test the conservative topology predicates and identify missing cases.
5. Produce a blind report with each claim tagged FACT / VERIFIED / INFERRED / ASSUMPTION; cite page and region for every proposed symbol, pin, wire, junction and label.
6. List all unresolved symbol anchors, pin anchors, junctions, label attachments and net identities. Do not silently resolve them.
7. Do not generate `.kicad_pcb`. Do not generate `.kicad_sch` unless you can independently prove the topology and validate it with a real KiCad parser/CLI. If not, explicitly block G3/G4/G5.
8. Freeze and hash your report before the reviewer opens `reference_holdout/`. Then compare only the overlapping reference records; the holdout is not complete connectivity ground truth.

Required deliverables: source hash, command logs, page coverage, primitive counts, OCR output, test results, discrepancies, unresolved list, gate decision, and SHA-256 manifest. Never report PASS without reproducible code and logs.
