# PDF reference connectivity → KiCad → round-trip gates

## What this pipeline does

~~~text
Original PDF
  ├─ Production parser → candidate connectivity ledger (NOT reference truth)
  └─ Direct PyMuPDF evidence → independent explicit-junction report
         |
         v
Complete page-specific review ledger (every candidate resolved)
         |
         v
Reviewed reference netlist (VERIFIED / NC only)
         |
         v
KiCad schematic generator
         |
         v
Separate KiCad S-expression parser / pin-net read-back
~~~

The independent PDF branch currently verifies explicit filled-junction marks from raw PDF vector drawings. It does not independently infer all component pins, labels, or complete net membership. Do not call it a complete electrical oracle.

## 1. Run all pages

From repository root:

~~~powershell
python tools/pdf2kicad/tools/roundtrip_pipeline.py "QI9-2604-A01_G3-G4_PAGE6_REFINEMENT/source/[QI9-2604-A01] Module IO.pdf" --pages 5-11 --outdir pdf2kicad-roundtrip
~~~

Outputs:
- page-NNN/: parser output and traceable wire/terminal evidence.
- reference-candidates/page-NNN.json: candidate ledger; every entry starts UNKNOWN.
- independent-pdf-evidence/page-NNN-junctions.json: direct raw-PDF junction evidence, separately derived from PyMuPDF drawing primitives.
- roundtrip_report.json: remains BLOCKED_REFERENCE_REVIEW_REQUIRED until a complete reviewed reference exists.

The candidate ledger contains attached terminals, open/unresolved terminals, and explicit NC mark candidates. Coordinate fallbacks are not copied into pin numbers.

## 2. Review every candidate

Create reviewed-reference/page-NNN.json from each candidate ledger. Keep the original candidate ledger unchanged. The review file must include these top-level fields:

~~~json
{
  "schema": "pdf2kicad-pdf-reference-connectivity/v1",
  "source_pdf_sha256": "<exact SHA-256 from candidate ledger>",
  "page": 5,
  "candidate_ledger_sha256": "<SHA-256 of the exact candidate JSON file>",
  "expected_entry_count": 1,
  "entries": []
}
~~~

The example count is illustrative only. Use the actual candidate count. Each reviewed entry must preserve source_candidate_index and contain:
- ref, pin, net;
- status: VERIFIED or NC;
- evidence.pdf_page;
- non-empty evidence.primitive_ids;
- evidence.review_note describing what was checked directly on the PDF.

Rules:
- Every candidate index must occur exactly once. Missing entries block export.
- All pins must be resolved. Ambiguous or unknown terminals block export.
- Explicit no-connect marks must be reviewed and recorded as NC, not as a net.
- Coordinate-shaped tokens are rejected as pin numbers.
- Duplicate physical (ref,pin) assignments and conflicting assignments are rejected.
- Candidate ledger hash and source PDF hash must match exactly.
- Never bulk-promote UNKNOWN to VERIFIED.

## 3. Generate KiCad only from the complete reviewed reference

~~~powershell
python tools/pdf2kicad/tools/roundtrip_pipeline.py "QI9-2604-A01_G3-G4_PAGE6_REFINEMENT/source/[QI9-2604-A01] Module IO.pdf" --pages 5-11 --outdir pdf2kicad-roundtrip --reference-dir reviewed-reference
~~~

For each page, the pipeline verifies source and candidate hashes, complete candidate-index coverage, pin identity, NC handling, and evidence. It then writes a netlist from the reviewed reference (never from parser-derived netlist.csv), generates .kicad_sch, and uses the separate verify_kicad_sch.py parser to read the saved schematic back and compare pin/net assignments.

## 4. Candidate schematic preview (not electrical sign-off)

CI also generates pdf2kicad_candidate_schematics/page-N/ from only non-coordinate parser pin candidates, preserving explicit parser NC candidates where pin tokens exist. It then reads each generated schematic back.

This is useful to test the KiCad writer and expose serialization/layout issues. It is explicitly marked UNVERIFIED:
- parser-derived memberships are not independent reference truth;
- coordinate fallback pins are excluded;
- generator places symbols by block, not at their original PDF positions;
- a read-back PASS means the saved KiCad structure matches its candidate input, not that the PDF was interpreted correctly.

## 5. Gate meanings

- BLOCKED_REFERENCE_REVIEW_REQUIRED: candidate ledgers exist; no electrical sign-off.
- DRAFT_READBACK_PASS_NOT_ELECTRICAL_SIGNOFF: candidate schematic generation and read-back succeeded; electrical correctness remains blocked.
- BLOCKED_REFERENCE_REVIEW_OR_READBACK: reviewed reference incomplete/invalid or KiCad read-back failed.
- PDF_REFERENCE_TO_KICAD_READBACK_PASS: all in-scope reviewed references cover every candidate and the saved KiCad files read back to the same pin/net assignments.

A final electrical sign-off still requires a complete reviewed reference and resolution of every ambiguity visible in the original PDF. KiCad ERC should be run on the resulting project as a separate design-rule check; ERC PASS alone is not proof of PDF equivalence.
