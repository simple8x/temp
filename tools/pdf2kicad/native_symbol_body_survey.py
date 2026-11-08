#!/usr/bin/env python3
"""Candidate-only native symbol-shape survey for schematic pages.

This deliberately does not consume a manual oracle and cannot assert symbol
ownership, pin identity, wire classification, or electrical connectivity.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pdf2kicad.independent_pdf_reconstruction import extract_independent_pdf_reconstruction
from pdf_symbol_body_candidates import discover_symbol_body_candidates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--pages", default="5-11", help="Comma-separated pages/ranges, e.g. 5-11")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    pages = []
    for token in args.pages.split(","):
        token = token.strip()
        if "-" in token:
            lo, hi = (int(x) for x in token.split("-", 1))
            if lo < 1 or hi < lo:
                raise SystemExit(f"invalid page range: {token}")
            pages.extend(range(lo, hi + 1))
        else:
            pages.append(int(token))
    pages = sorted(set(pages))
    digest = hashlib.sha256(Path(args.pdf).read_bytes()).hexdigest()
    reports = []
    for page_no in pages:
        ir = extract_independent_pdf_reconstruction(args.pdf, [page_no])
        refs = ir["page_results"][0]["references"]
        result = discover_symbol_body_candidates(args.pdf, page_no, refs)
        candidate_pairs = sum(len(row["matches"]) for row in result["references"])
        reports.append({
            "page": page_no,
            "reference_count": len(refs),
            "native_shape_count": result["native_shape_count"],
            "references_with_candidates": sum(bool(row["matches"]) for row in result["references"]),
            "candidate_associations": candidate_pairs,
            "references": result["references"],
            "policy": result["policy"],
        })
    out = {
        "schema": "qi9-native-symbol-shape-survey/v1",
        "source_sha256": digest,
        "pages": pages,
        "page_reports": reports,
        "totals": {
            "pages": len(reports),
            "references": sum(x["reference_count"] for x in reports),
            "native_shapes": sum(x["native_shape_count"] for x in reports),
            "references_with_candidates": sum(x["references_with_candidates"] for x in reports),
            "candidate_associations": sum(x["candidate_associations"] for x in reports),
        },
        "decision": "CANDIDATE_ONLY_NO_OWNERSHIP_OR_ELECTRICAL_FACTS",
        "electrical_signoff": "BLOCKED",
    }
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=2) + "\\n", encoding="utf-8")
    print(json.dumps({"source_sha256": digest, "pages": pages, "totals": out["totals"], "decision": out["decision"]}, indent=2))


if __name__ == "__main__":
    main()
