#!/usr/bin/env python3
"""Forensic probe for Visio-printed schematic PDFs.

OCR results are candidates only. This script never infers electrical connectivity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

import fitz

REF_RE = re.compile(r"\b(RN|SW|C|D|F|J|L|Q|R|U|Y)\s*(\d{1,3})\b", re.I)


def refs_from_text(text: str) -> list[str]:
    return sorted({f"{a.upper()}{int(b)}" for a, b in REF_RE.findall(text)})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--out", type=Path, default=Path("pdf-forensic-probe.json"))
    ap.add_argument("--first-page", type=int, default=5, help="1-based")
    ap.add_argument("--last-page", type=int, default=11, help="1-based")
    ap.add_argument("--scale", type=float, default=2.5)
    args = ap.parse_args()

    raw = args.pdf.read_bytes()
    doc = fitz.open(stream=raw, filetype="pdf")
    bom_text = "\n".join(doc[i].get_text() for i in range(1, min(4, len(doc))))
    expected = refs_from_text(bom_text)
    page_rows = []

    for page_no in range(args.first_page, args.last_page + 1):
        page = doc[page_no - 1]
        drawings = page.get_drawings()
        text_dict = page.get_text("dict")
        spans = [
            span
            for block in text_dict["blocks"] if "lines" in block
            for line in block["lines"] for span in line["spans"]
        ]
        item_types = Counter(item[0] for drawing in drawings for item in drawing["items"])
        pix = page.get_pixmap(matrix=fitz.Matrix(args.scale, args.scale), alpha=False)
        image_path = args.out.parent / f"{args.out.stem}-page-{page_no}.png"
        pix.save(image_path)
        proc = subprocess.run(
            ["tesseract", str(image_path), "stdout", "--psm", "11"],
            check=True, capture_output=True, text=True,
        )
        ocr_text = proc.stdout
        refs = refs_from_text(ocr_text)
        page_rows.append({
            "page": page_no,
            "vector_drawing_count": len(drawings),
            "vector_item_count": sum(len(d["items"]) for d in drawings),
            "vector_item_types": dict(item_types),
            "selectable_text_span_count": len(spans),
            "selectable_text_char_count": sum(len(s["text"]) for s in spans),
            "ocr_reference_candidates": refs,
            "ocr_raw_text": ocr_text,
        })

    found = sorted({r for row in page_rows for r in row["ocr_reference_candidates"]})
    matched = sorted(set(expected) & set(found))
    result = {
        "source_pdf": str(args.pdf),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "page_count": len(doc),
        "schematic_pages": [args.first_page, args.last_page],
        "bom_reference_count": len(expected),
        "bom_references": expected,
        "ocr_unique_reference_candidates": len(found),
        "ocr_exact_bom_matches": len(matched),
        "ocr_candidate_recall_against_bom": len(matched) / len(expected) if expected else None,
        "ocr_candidates": found,
        "bom_refs_not_seen_by_ocr": sorted(set(expected) - set(found)),
        "ocr_candidates_not_in_bom": sorted(set(found) - set(expected)),
        "pages": page_rows,
        "reference_verdict": "CANDIDATES_ONLY_NOT_VERIFIED",
        "connectivity_verdict": "NOT_EVALUATED",
        "limitations": [
            "OCR text is a candidate source, not verified component identity.",
            "BOM membership does not establish that a reference appears on a schematic page.",
            "No electrical connectivity is inferred by this probe.",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "pages" and k != "ocr_raw_text"}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
