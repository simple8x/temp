#!/usr/bin/env python3
"""Independent OCR-TSV + PDF-vector evidence fusion for QI9.

OCR tokens and nearby vector geometry are candidate evidence only. This module
never assigns symbol ownership, pin numbers, wires, nets, or verified membership.
Coordinates are transformed from raster pixels to PDF points using each rendered
page's actual dimensions, not a guessed DPI constant.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import subprocess
from collections import Counter
from pathlib import Path

import fitz

REF_RE = re.compile(r"^(?:RN|SW|[A-Z])\d+[A-Z]?$", re.I)
PIN_RE = re.compile(r"^(?:\d{1,3}|[A-Z]{1,2}\d{0,2}|[A-Z][+-]?)$", re.I)
NET_RE = re.compile(r"^(?:GND|VCC|VIN|VOUT|TX[A-Z0-9_-]*|RX[A-Z0-9_-]*|[+-]?\d+(?:\.\d+)?(?:k|r|m|u|n|p|v|a|f|h))$", re.I)


def point_segment_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    den = dx * dx + dy * dy
    if den == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0]-a[0])*dx + (p[1]-a[1])*dy) / den))
    return math.hypot(p[0] - (a[0] + t*dx), p[1] - (a[1] + t*dy))


def classify_token(token):
    t = token.strip()
    if REF_RE.fullmatch(t):
        return "REFERENCE_CANDIDATE"
    if PIN_RE.fullmatch(t) and not NET_RE.fullmatch(t):
        return "PIN_TEXT_CANDIDATE"
    if NET_RE.fullmatch(t):
        return "NET_LABEL_CANDIDATE"
    return "OTHER_TEXT"


def parse_tsv(tsv_text, page_no, pix_width, pix_height, page_width_pt, page_height_pt, min_conf=25.0):
    sx, sy = page_width_pt / pix_width, page_height_pt / pix_height
    rows = []
    for row in csv.DictReader(io.StringIO(tsv_text), delimiter="\t"):
        text = (row.get("text") or "").strip()
        try:
            conf = float(row.get("conf", -1))
            left, top = int(row["left"]), int(row["top"])
            width, height = int(row["width"]), int(row["height"])
        except (ValueError, KeyError, TypeError):
            continue
        kind = classify_token(text)
        if not text or conf < min_conf or kind == "OTHER_TEXT" or width <= 0 or height <= 0:
            continue
        box = [left*sx, top*sy, (left+width)*sx, (top+height)*sy]
        rows.append({
            "page": page_no, "text": text, "kind": kind, "confidence": round(conf, 3),
            "bbox_pdf_pt": [round(v, 4) for v in box],
            "center_pdf_pt": [round((box[0]+box[2])/2, 4), round((box[1]+box[3])/2, 4)],
            "source": "tesseract_tsv",
        })
    return rows


def extract_segments(page):
    segs = []
    for path_i, drawing in enumerate(page.get_drawings()):
        for item_i, item in enumerate(drawing.get("items", [])):
            pairs = []
            if item[0] == "l":
                pairs = [(tuple(item[1]), tuple(item[2]))]
            elif item[0] == "re":
                r = item[1]
                pts = [(r.x0,r.y0),(r.x1,r.y0),(r.x1,r.y1),(r.x0,r.y1)]
                pairs = list(zip(pts, pts[1:]+pts[:1]))
            for edge_i, (a,b) in enumerate(pairs):
                length = math.hypot(b[0]-a[0], b[1]-a[1])
                if length < 0.5:
                    continue
                segs.append({
                    "id": len(segs), "path_index": path_i, "item_index": item_i,
                    "edge_index": edge_i, "a": [a[0],a[1]], "b": [b[0],b[1]],
                    "length_pt": round(length,4),
                    "axis": "H" if abs(a[1]-b[1]) <= .35 else "V" if abs(a[0]-b[0]) <= .35 else "OBLIQUE",
                    "stroke_width_pt": drawing.get("width"),
                    "electrical_class": "UNKNOWN_VECTOR_STROKE",
                })
    return segs


def fuse_page(page_no, page, ocr_rows, radius_pt=12.0):
    segments = extract_segments(page)
    for token in ocr_rows:
        p = token["center_pdf_pt"]
        ranked = sorted(
            ((point_segment_distance(p, s["a"], s["b"]), s) for s in segments),
            key=lambda x: x[0],
        )
        near = [{"segment_id": s["id"], "distance_pt": round(d,4), "axis": s["axis"],
                 "length_pt": s["length_pt"]} for d,s in ranked[:5] if d <= radius_pt]
        token["nearby_vector_candidates"] = near
        token["geometry_relation"] = (
            "NEAR_VECTOR_CANDIDATE" if near else "NO_VECTOR_WITHIN_RADIUS"
        )
        token["symbol_ownership"] = "UNKNOWN"
        token["pin_number_identity"] = "UNKNOWN"
        token["electrical_membership"] = "UNKNOWN"
    return {
        "page": page_no, "page_size_pt": [page.rect.width, page.rect.height],
        "vector_segment_count": len(segments),
        "ocr_token_count": len(ocr_rows),
        "reference_candidates": sum(x["kind"] == "REFERENCE_CANDIDATE" for x in ocr_rows),
        "pin_text_candidates": sum(x["kind"] == "PIN_TEXT_CANDIDATE" for x in ocr_rows),
        "net_label_candidates": sum(x["kind"] == "NET_LABEL_CANDIDATE" for x in ocr_rows),
        "segments": segments, "tokens": ocr_rows,
    }


def run(pdf_path, pages, outdir, scale=3.0, min_conf=25.0, radius_pt=12.0):
    pdf_path, outdir = Path(pdf_path), Path(outdir)
    raw = pdf_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    doc = fitz.open(stream=raw, filetype="pdf")
    outdir.mkdir(parents=True, exist_ok=True)
    page_results = []
    for page_no in pages:
        page = doc[page_no-1]
        pix = page.get_pixmap(matrix=fitz.Matrix(scale,scale), alpha=False)
        proc = subprocess.run(["tesseract","stdin","stdout","--psm","11","tsv"],
                              input=pix.tobytes("png"), capture_output=True, check=True)
        rows = parse_tsv(proc.stdout.decode("utf-8",errors="replace"), page_no,
                         pix.width,pix.height,page.rect.width,page.rect.height,min_conf)
        page_results.append(fuse_page(page_no,page,rows,radius_pt))
    doc.close()
    counts = Counter()
    for p in page_results:
        counts.update({k:p[k] for k in ("vector_segment_count","ocr_token_count",
                                        "reference_candidates","pin_text_candidates","net_label_candidates")})
    result = {
        "schema":"qi9-independent-ocr-vector-fusion/v1",
        "source_pdf":pdf_path.name,"source_sha256":digest,"pages":pages,
        "coordinate_transform":"pixel_bbox * (page_rect_points / rendered_pixmap_pixels)",
        "parameters":{"render_scale":scale,"min_ocr_confidence":min_conf,"geometry_radius_pt":radius_pt},
        "summary":dict(counts),
        "authority":{
            "ocr_text":"CANDIDATE_ONLY","vector_geometry":"CANDIDATE_ONLY",
            "symbol_ownership":"UNKNOWN","pin_identity":"UNKNOWN",
            "electrical_connectivity":"NOT_EVALUATED",
            "auto_promotion":False,
        },
        "signoff":"BLOCKED_NO_VERIFIED_SYMBOL_PIN_OR_ELECTRICAL_MEMBERSHIP",
        "pages_result":page_results,
    }
    (outdir/"independent_ocr_vector_fusion.json").write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    summary={k:v for k,v in result.items() if k!="pages_result"}
    print(json.dumps(summary,indent=2,ensure_ascii=False))
    return result


def self_test():
    assert classify_token("U5") == "REFERENCE_CANDIDATE"
    assert classify_token("12") == "PIN_TEXT_CANDIDATE"
    assert classify_token("GND") == "NET_LABEL_CANDIDATE"
    rows=parse_tsv("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n5\t1\t1\t1\t1\t1\t10\t20\t30\t10\t90\t12\n",
                   5,100,100,200,200)
    assert rows[0]["bbox_pdf_pt"] == [20.0,40.0,80.0,60.0]
    assert abs(point_segment_distance((5,4),(0,0),(10,0))-4)<1e-9
    print("SELF_TEST=PASS")


if __name__ == "__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--pdf")
    ap.add_argument("--outdir",default="qi9-independent-fusion")
    ap.add_argument("--pages",nargs="+",type=int,default=list(range(5,12)))
    ap.add_argument("--scale",type=float,default=3.0)
    ap.add_argument("--min-confidence",type=float,default=25.0)
    ap.add_argument("--geometry-radius-pt",type=float,default=12.0)
    ap.add_argument("--self-test",action="store_true")
    a=ap.parse_args()
    if a.self_test: self_test()
    elif a.pdf: run(a.pdf,a.pages,a.outdir,a.scale,a.min_confidence,a.geometry_radius_pt)
    else: ap.error("--pdf required unless --self-test")
