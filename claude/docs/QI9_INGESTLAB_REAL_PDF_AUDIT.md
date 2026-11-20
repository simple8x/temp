# QI9 real-PDF IngestLab audit

## Scope and source identity

- Source: `[QI9-2604-A01] Module IO.pdf` (14 pages).
- SHA-256: `a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc`.
- The source PDF is not committed to this repository.
- IngestLab: `ingestlab-dev/ingestlab` v`1.1.3`, checkout commit `73f530c7415b815a8b6297ca24aad65a1351ca11`.
- Runtime: Node.js in Box; no GPU was used.
- Parse options: `ocr=true`, `ocrImages=true`, `vision=false`, `figures=false`, `cache=false`, `imageMode=none`, `dropHeadersFooters=false`, `markdownProfile=faithful`.
- Independent page dimensions came from PyMuPDF, not from IngestLab.

## IngestLab parse result

Observed output from the real PDF:

| Metric | Result |
|---|---:|
| Pages | 14 |
| Words | 1,157 |
| Lines | 285 |
| Headings | 39 |
| Tables | 17 |
| Lists | 6 |
| Code blocks | 0 |
| Forms | 3 |
| IngestLab OCR pages | 0 |
| OCR failures on whole pages | 0 |
| Scanned pages marked unread | 0 |
| Embedded-image OCR failures | 3 |
| ParseResult blocks | 212 |
| Markdown characters | 9,496 |
| Reported parse time | 2,225.2 ms |

The zero unread-scan count does not establish that the schematic labels or electrical topology were fully recognized. The embedded-image OCR path logged `createCanvas` unavailable for three regions.

## Adapter result on the real ParseResult

The adapter now explicitly accounts for list, code, form-section, empty, and unsupported block types. It no longer silently skips unsupported blocks.

| Metric | Result |
|---|---:|
| Candidate evidence items | 23 |
| Unresolved items | 189 |
| Source blocks seen | 212 |
| Source blocks accounted | 212 |
| Block accounting complete | true |
| VERIFIED items | 0 |
| Connectivity claims | 0 |

All 189 unresolved blocks have reason `source bbox is missing`. The adapter exits with status 2 when unresolved items remain; this is the intended fail-closed result, not a passing evidence export.

## Independent OCR and BOM reconciliation

Three Tesseract passes (same engine, different page-segmentation modes and render scales/crops) were run on rendered schematic pages 5–11:

- Tesseract 5, PSM 11, 3x full-page render: 1,448 word observations; 1,019 with confidence >= 50.
- Tesseract 5, PSM 6, 6x full-page render: 1,783 word observations; 1,158 with confidence >= 50.
- Tesseract 5, PSM 6, 6x schematic-body crop: 1,594 word observations; 958 with confidence >= 50.

These counts are per-pass observations and must not be added as unique words. OCR boxes and confidence are retained as UNKNOWN evidence.

The BOM reference list was extracted from the PDF's native text layer on pages 2–4:

- 209 unique component references in the BOM.
At confidence >= 30, after BOM-aware normalization of recognized IC unit suffixes:
- 185 unique OCR reference candidates.
- 174 candidates match a BOM reference; 11 remain unmatched.
- 35 BOM references were not detected.
- Candidate recall: 83.25%; candidate precision: 94.05%.

At confidence >= 50:
- 176 unique OCR reference candidates.
- 168 candidates match a BOM reference; 8 remain unmatched.
- 41 BOM references were not detected.
- Candidate recall: 80.38%; candidate precision: 95.45%.

This is reference-name cross-checking only. It does not prove that a component symbol or pin was correctly located, nor does it establish any net membership.

## Vector forensics

PyMuPDF independently inspected schematic pages 5–11:

- 68,400 vector drawing paths.
- 70,286 straight-line primitives.
- A heuristic filter using all three OCR passes produced 1,762 wire-like line candidates after text-box masking.
- Endpoint clustering and endpoint-to-segment analysis remain candidate geometry only. Crossings, junction dots, symbol pins, and label attachment have not been independently resolved.

The current candidate graph is not a netlist and must not be used to create or modify ECCI nets.

## Decision

**IngestLab ran on the real QI9 PDF. The evidence adapter is fail-closed and block-accounting complete. Electrical connectivity remains BLOCKED.**

Outstanding work before any electrical promotion:

1. Recover vector text/reference glyph geometry and independently validate component-reference boxes.
2. Build a planar arrangement graph that preserves path and primitive provenance.
3. Resolve pin terminals and wire junction semantics, including crossing-without-junction cases.
4. Compare every proposed terminal/net membership with the rendered source and an independent oracle.
5. Keep all unproven memberships UNKNOWN; no automatic promotion to VERIFIED.

No electrical net was created or modified. No connectivity was promoted. No production ECCI writer was invoked.
