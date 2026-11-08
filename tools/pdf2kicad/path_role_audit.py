#!/usr/bin/env python3
"""Run path-grouped classification diagnostics on one source PDF page."""
import argparse, hashlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pdf2kicad.independent_pdf_reconstruction import extract_independent_pdf_reconstruction
from path_role_classifier import classify_path_groups

EXPECTED = "a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, default=6)
    ap.add_argument("--out", required=True)
    args=ap.parse_args()
    raw=Path(args.pdf).read_bytes()
    digest=hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED:
        raise SystemExit(f"source SHA mismatch: {digest}")
    ir=extract_independent_pdf_reconstruction(args.pdf,[args.page])
    page=ir["page_results"][0]
    result=classify_path_groups(page["primitive_inventory"])
    report={
      "schema":"qi9-path-role-audit/v1",
      "source_sha256":digest,
      "page":args.page,
      "source_primitives":len(page["primitive_inventory"]),
      "raw_path_count":len({p["path_index"] for p in page["primitive_inventory"]}),
      "classifier":result,
      "evaluation":{
        "manual_ground_truth_rows":0,
        "precision":None,
        "recall":None,
        "confusion_matrix":None,
        "status":"BLOCKED_GROUND_TRUTH_NOT_AUTHORED",
      },
      "decision":"DIAGNOSTIC_ONLY_NOT_A_PRODUCTION_CLASSIFIER",
    }
    Path(args.out).parent.mkdir(parents=True,exist_ok=True)
    Path(args.out).write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({
      "page":args.page,"source_primitives":report["source_primitives"],
      "raw_path_count":report["raw_path_count"],
      "path_role_counts":result["primitive_role_counts"],
      "ground_truth_status":report["evaluation"]["status"],
      "decision":report["decision"],
    },indent=2))

if __name__=="__main__": main()
