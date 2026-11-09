#!/usr/bin/env python3
"""Build a human-review pack for native symbol-body candidates on QI9 pages 5-11.

The overlay visualizes algorithmic candidates only. It intentionally leaves every
manual label blank and never writes electrical connectivity facts.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import pymupdf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pdf2kicad.independent_pdf_reconstruction import extract_independent_pdf_reconstruction
from pdf_symbol_body_candidates import discover_symbol_body_candidates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--pages", default="5-11")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--dpi", type=int, default=100)
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
    pdf_path = Path(args.pdf)
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    doc = pymupdf.open(pdf_path)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    review_rows = []

    for page_no in pages:
        ir = extract_independent_pdf_reconstruction(str(pdf_path), [page_no])
        refs = ir["page_results"][0]["references"]
        result = discover_symbol_body_candidates(str(pdf_path), page_no, refs)
        page = doc[page_no - 1]
        mb = page.mediabox
        page_width_points = float(mb.width)
        page_height_points = float(mb.height)
        scale = args.dpi / 72.0
        clean_pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        clean_image_path = outdir / f"page_{page_no:02d}_source.png"
        clean_pix.save(clean_image_path)
        page_rows = []
        for row in result["references"]:
            ref = row["reference"]
            rb = row["reference_bbox"]
            ref_rect = pymupdf.Rect(rb[0] + mb.x0, rb[1] + mb.y0,
                                    rb[2] + mb.x0, rb[3] + mb.y0)
            page.draw_rect(ref_rect, color=(0.9, 0.15, 0.1), width=0.8, overlay=True)
            best = row.get("best_match")
            candidate = None
            if best:
                b = best["bbox"]
                candidate = [round(float(v), 4) for v in b]
                shape_rect = pymupdf.Rect(b[0] + mb.x0, b[1] + mb.y0,
                                          b[2] + mb.x0, b[3] + mb.y0)
                page.draw_rect(shape_rect, color=(0.05, 0.55, 0.2), width=1.1, overlay=True)
            page_rows.append({
                "reference": ref,
                "reference_bbox": rb,
                "best_candidate_path_id": best.get("path_id") if best else None,
                "best_candidate_bbox": candidate,
                "candidate_count": len(row["matches"]),
                "manual_label": None,
                "manual_path_id": None,
                "review_status": "NOT_REVIEWED",
            })
            review_rows.append({"page": page_no, **page_rows[-1]})

        scale = args.dpi / 72.0
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        image_path = outdir / f"page_{page_no:02d}_candidate_overlay.png"
        pix.save(image_path)
        (outdir / f"page_{page_no:02d}_review_template.json").write_text(
            json.dumps({
                "schema": "qi9-manual-symbol-review-template/v1",
                "source_sha256": digest,
                "page": page_no,
                "page_size_points": [page_width_points, page_height_points],
                "render_dpi": args.dpi,
                "source_image": clean_image_path.name,
                "candidate_overlay_image": f"page_{page_no:02d}_candidate_overlay.png",
                "policy": "Clean source image is the default blind-review surface; candidate overlays are optional and are not ground truth.",
                "references": page_rows,
            }, indent=2) + "\n", encoding="utf-8"
        )

    doc.close()
    index = {
        "schema": "qi9-symbol-review-pack/v1",
        "source_sha256": digest,
        "pages": pages,
        "render_dpi": args.dpi,
        "page_metadata": {
            str(p): {
                "page_size_points": [
                    float(doc[p - 1].mediabox.width),
                    float(doc[p - 1].mediabox.height)
                ],
                "source_image": f"page_{p:02d}_source.png",
                "candidate_overlay_image": f"page_{p:02d}_candidate_overlay.png"
            } for p in pages
        },
        "reference_rows": len(review_rows),
        "reviewed_rows": 0,
        "manual_labels_written": 0,
        "electrical_facts_written": 0,
        "decision": "HUMAN_REVIEW_REQUIRED_NO_ORACLE_LABELS_INFERRED",
        "rows": review_rows,
    }
    (outdir / "manual_review_index.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: index[k] for k in (
        "source_sha256", "pages", "reference_rows", "reviewed_rows",
        "manual_labels_written", "electrical_facts_written", "decision")}, indent=2))


if __name__ == "__main__":
    main()
