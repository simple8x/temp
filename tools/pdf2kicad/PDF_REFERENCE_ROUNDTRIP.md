# PDF reference connectivity → KiCad → read-back gate

~~~text
Original PDF
  └─ pdf2kicad candidate extraction (not trusted as truth)
       └─ page-NNN.json review ledger (PDF coordinates + primitive lineage)
            └─ human verification against the rendered/vector PDF
                 └─ reviewed reference connectivity (VERIFIED / NC only)
                      └─ netlist.csv generated from reviewed reference
                           └─ gen_kicad_sch.py
                                └─ verify_kicad_sch.py reads .kicad_sch independently
                                     └─ compare read-back with reviewed PDF reference
~~~

## 1. Generate review candidates from the pinned PDF

From repository root:

~~~powershell
python tools/pdf2kicad/tools/roundtrip_pipeline.py "QI9-2604-A01_G3-G4_PAGE6_REFINEMENT/source/[QI9-2604-A01] Module IO.pdf" --pages 5-11 --outdir pdf2kicad-roundtrip
~~~

Outputs:
- pdf2kicad-roundtrip/page-NNN/: ordinary parser output and traceable evidence.
- pdf2kicad-roundtrip/reference-candidates/page-NNN.json: review worksheet containing PDF page, terminal coordinates, candidate net/ref/pin and source wire primitive lineage.
- pdf2kicad-roundtrip/roundtrip_report.json: expected status BLOCKED_REFERENCE_REVIEW_REQUIRED.

Candidate net/ref/pin values are hints only. Every candidate starts UNKNOWN; coordinate fallback is never copied into pin. Do not promote candidates by bulk-changing status.

## 2. Review each candidate against the PDF

Create reviewed-reference/page-NNN.json from the candidate file. For every electrical terminal:
- inspect the exact PDF page and cited coordinates/primitive lineage;
- correct ref, pin, and net using visible evidence;
- set status to VERIFIED only when the pin identity and its electrical connection are unambiguous;
- set status to NC only when an explicit no-connect mark is visible;
- add evidence.review_note describing what was visually checked;
- ensure evidence.primitive_ids is non-empty.

Unresolved or ambiguous entries must remain unresolved. The gate rejects them. Also reject coordinate-shaped pin strings, duplicate (ref,pin) assignments, wrong PDF hashes, wrong pages, and references without review evidence.

Reference entry shape:

~~~json
{
  "ref": "U1",
  "pin": "3",
  "net": "GND",
  "status": "VERIFIED",
  "evidence": {
    "pdf_page": 5,
    "primitive_ids": ["17", "18"],
    "review_note": "Pin 3 label and continuous wire path inspected on source PDF page 5."
  }
}
~~~

This example is a schema example only; it is not a claim about the actual QI9 pinout.

## 3. Generate KiCad only from reviewed reference

~~~powershell
python tools/pdf2kicad/tools/roundtrip_pipeline.py "QI9-2604-A01_G3-G4_PAGE6_REFINEMENT/source/[QI9-2604-A01] Module IO.pdf" --pages 5-11 --outdir pdf2kicad-roundtrip --reference-dir reviewed-reference
~~~

For each page, the pipeline:
1. verifies the reviewed reference is bound to the exact source PDF SHA-256 and page;
2. rejects UNKNOWN/AMBIGUOUS entries, coordinate pin tokens, duplicate physical pins, and missing evidence;
3. creates the generator input netlist from the reviewed reference, not parser-derived netlist.csv;
4. generates .kicad_sch;
5. invokes a separate parser (verify_kicad_sch.py) to read the saved schematic and compare its pin/net assignments against the reviewed reference netlist.

The parser-derived pins.csv is deliberately not supplied to the generator, so parser-inferred pin identities cannot silently override reviewed pin identities.

## 4. Gate meanings and limits

- BLOCKED_REFERENCE_REVIEW_REQUIRED: candidate ledgers exist; no electrical sign-off.
- BLOCKED_REFERENCE_REVIEW_OR_READBACK: reference incomplete/invalid or KiCad read-back failed.
- PDF_REFERENCE_TO_KICAD_READBACK_PASS: all supplied pages have reviewed references and the saved KiCad files read back to the same pin/net assignments.

A read-back PASS proves the generated KiCad file preserves the reviewed PDF-derived reference. It does not prove a human reviewer interpreted an inherently ambiguous PDF correctly. The current KiCad verifier checks the generated label-at-pin representation; it is not a full KiCad ERC engine and does not independently infer the source PDF's electrical semantics. Electrical sign-off still requires reviewing every PDF-derived reference entry.
