#!/usr/bin/env python3
"""Run independent PDF forensic inventory without deriving electrical nets."""
import argparse, hashlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pdf2kicad.independent_pdf_reconstruction import extract_independent_pdf_reconstruction
from pdf2kicad.electrical_evidence import classify_pages

EXPECTED="a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc"
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--pdf",required=True); p.add_argument("--outdir",required=True)
    a=p.parse_args(); pdf=Path(a.pdf); out=Path(a.outdir); out.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
    if digest != EXPECTED: raise SystemExit(f"source hash mismatch: {digest}")
    inv=extract_independent_pdf_reconstruction(str(pdf),list(range(5,12)))
    (out/"independent_pdf_forensic_ir.json").write_text(json.dumps(inv,indent=2)+"\n")
    junctions=classify_pages(pdf,range(5,12),out/"junction_evidence")
    summary={"source_sha256":digest,"pages":7,
      "reference_candidates":inv["summary"].get("reference_candidates",0),
      "stroke_font_text_words":inv["summary"].get("stroke_font_text_words",0),
      "stroke_font_reference_candidates":inv["summary"].get("stroke_font_reference_candidates",0),
      "stroke_font_pin_token_candidates":inv["summary"].get("stroke_font_pin_token_candidates",0),
      "raw_text_layer_words":inv["summary"].get("raw_text_layer_words",0),
      "vector_primitives":inv["summary"].get("vector_primitives",0),
      "junction_marks_confirmed":sum(x["summary"]["explicit_junctions_confirmed"] for x in junctions),
      "verified_symbol_ownership":0,"verified_pin_identity":0,
      "verified_electrical_memberships":0,
      "electrical_signoff":"BLOCKED_NO_PDF_GROUND_TRUTH_FOR_SYMBOL_PIN_AND_WIRE_CLASSIFICATION"}
    (out/"independent_pdf_audit_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    print(json.dumps(summary,indent=2))
if __name__=="__main__": main()
