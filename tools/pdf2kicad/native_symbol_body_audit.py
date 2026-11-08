#!/usr/bin/env python3
"""Audit native quad/rect to reference associations against page-6 manual labels."""
import argparse,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from pdf2kicad.independent_pdf_reconstruction import extract_independent_pdf_reconstruction
from pdf_symbol_body_candidates import discover_symbol_body_candidates

def main():
 ap=argparse.ArgumentParser(); ap.add_argument("--pdf",required=True); ap.add_argument("--page",type=int,default=6); ap.add_argument("--oracle",required=True); ap.add_argument("--out",required=True); a=ap.parse_args()
 digest=hashlib.sha256(Path(a.pdf).read_bytes()).hexdigest()
 ir=extract_independent_pdf_reconstruction(a.pdf,[a.page])
 refs=ir["page_results"][0]["references"]
 result=discover_symbol_body_candidates(a.pdf,a.page,refs)
 oracle=json.loads(Path(a.oracle).read_text(encoding="utf-8"))
 if oracle["source_sha256"]!=digest or oracle["page"]!=a.page: raise SystemExit("manual oracle source/page mismatch")
 labels=oracle["labels"]; byref={x["reference"]:x for x in result["references"]}
 evaluated=[]
 for gold in labels:
  row=byref.get(gold["reference"]); best=row.get("best_match") if row else None
  got=best["path_id"] if best else None
  evaluated.append({"reference":gold["reference"],"expected_path_id":gold["path_id"],"predicted_path_id":got,"match":got==gold["path_id"],"candidate_count":len(row["matches"]) if row else 0})
 tp=sum(x["match"] for x in evaluated)
 report={"schema":"qi9-native-symbol-body-audit/v1","source_sha256":digest,"page":a.page,
 "native_symbol_candidates":result,"manual_oracle":oracle,
 "evaluation":{"manual_labels":len(labels),"correct_associations":tp,"exact_association_accuracy":tp/len(labels) if labels else None,"rows":evaluated,"scope":"symbol boundary association only; not wire/pin/net accuracy"},
 "decision":"CANDIDATE_ASSOCIATION_DIAGNOSTIC_ONLY" if tp==len(labels) else "BLOCKED_ASSOCIATION_MISMATCH",
 "electrical_signoff":"BLOCKED"}
 Path(a.out).parent.mkdir(parents=True,exist_ok=True); Path(a.out).write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
 print(json.dumps({"page":a.page,"native_shape_count":result["native_shape_count"],"manual_labels":len(labels),"correct":tp,"evaluated":evaluated,"decision":report["decision"]},indent=2))
if __name__=="__main__": main()
