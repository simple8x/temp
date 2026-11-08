"""PDF-native forensic reconstruction, independent of parser terminal ledgers.

The output is an independently discovered inventory of text, reference-designator
candidates, vector primitives, and geometric terminal candidates. Geometry alone
never establishes electrical connectivity. All unresolved classifications remain
UNKNOWN/CANDIDATE and this module deliberately emits no verified net memberships.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf

from pdf2kicad.vector_text_audit import decode_vector_words

REF_RE = re.compile(r"^(?:RN|SW|[A-Z])\d+[A-Z]?$", re.I)
PIN_RE = re.compile(r"^(?:\d{1,2}|[A-Z](?:[+-])?)$", re.I)


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _bbox_union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _inside_or_near(point, box, radius):
    x, y = point
    return (box[0] - radius <= x <= box[2] + radius
            and box[1] - radius <= y <= box[3] + radius)


def extract_independent_pdf_reconstruction(pdf_path: str, pages: list[int],
                                           *, symbol_context_pt: float = 72.0,
                                           endpoint_tolerance_pt: float = 0.35) -> dict:
    """Discover PDF objects without reading production parser or review-ledger data."""
    path = Path(pdf_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    doc = pymupdf.open(pdf_path)
    page_results = []

    for page_no in pages:
        if page_no < 1 or page_no > len(doc):
            raise ValueError(f"page out of range: {page_no}")
        page = doc[page_no - 1]
        media = page.mediabox
        ox, oy = media.x0, media.y0

        def xy(p):
            return [round(float(p.x - ox), 4), round(float(p.y - oy), 4)]

        words = []
        for word in page.get_text("words"):
            token = str(word[4]).strip()
            if not token:
                continue
            box = [round(word[0] - ox, 4), round(word[1] - oy, 4),
                   round(word[2] - ox, 4), round(word[3] - oy, 4)]
            words.append({
                "text": token, "bbox": box,
                "reference_candidate": bool(REF_RE.fullmatch(token)),
                "pin_token_candidate": bool(PIN_RE.fullmatch(token) and not REF_RE.fullmatch(token)),
                "source": "pymupdf_raw_pdf_text",
            })

        primitives, dots = [], []
        for path_index, drawing in enumerate(page.get_drawings()):
            rect = drawing.get("rect")
            fill = drawing.get("fill")
            if (fill is not None and rect is not None
                    and max(rect.width, rect.height) <= 3.0
                    and min(rect.width, rect.height) >= 0.6):
                dots.append({
                    "center": xy(pymupdf.Point((rect.x0 + rect.x1) / 2,
                                               (rect.y0 + rect.y1) / 2)),
                    "bbox": [rect.x0 - ox, rect.y0 - oy, rect.x1 - ox, rect.y1 - oy],
                    "classification": "JUNCTION_MARK_CANDIDATE",
                    "authority": "CANDIDATE_ONLY",
                    "path_index": path_index,
                })
            for item_index, item in enumerate(drawing.get("items", [])):
                edges = []
                if item[0] == "l":
                    edges = [(item[1], item[2])]
                elif item[0] == "re":
                    r = item[1]
                    pts = [pymupdf.Point(r.x0, r.y0), pymupdf.Point(r.x1, r.y0),
                           pymupdf.Point(r.x1, r.y1), pymupdf.Point(r.x0, r.y1)]
                    edges = list(zip(pts, pts[1:] + pts[:1]))
                for edge_index, (pa, pb) in enumerate(edges):
                    a, b = xy(pa), xy(pb)
                    length = _dist(a, b)
                    if length <= 1e-5:
                        continue
                    dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
                    axis = "H" if dy <= endpoint_tolerance_pt else (
                        "V" if dx <= endpoint_tolerance_pt else "OBLIQUE")
                    primitives.append({
                        "id": len(primitives), "path_index": path_index,
                        "item_index": item_index, "edge_index": edge_index,
                        "a": a, "b": b, "length_pt": round(length, 4),
                        "axis": axis,
                        "stroke_width_pt": drawing.get("width"),
                        "stroke_color": drawing.get("color"),
                        "fill_color": drawing.get("fill"),
                        "classification": (
                            "WIRE_OR_GRAPHIC_CANDIDATE" if axis in ("H", "V") and length >= 3.0
                            else "SYMBOL_OR_GRAPHIC_CANDIDATE" if axis == "OBLIQUE"
                            else "UNKNOWN"
                        ),
                        "electrical_authority": "NOT_ESTABLISHED",
                    })

        # The source schematic uses KiCad newstroke vector text for many labels.
        # Decode those strokes independently of the production terminal ledger and
        # merge them into this inventory. They remain text candidates, never pin facts.
        vector_words = decode_vector_words(pdf_path, page_no)
        raw_keys = {(w["text"], tuple(round(v, 2) for v in w["bbox"])) for w in words}
        for vw in vector_words:
            key = (vw["text"], tuple(round(v, 2) for v in vw["bbox"]))
            if key in raw_keys:
                continue
            words.append({
                "text": vw["text"], "bbox": vw["bbox"],
                "reference_candidate": vw["reference_token_candidate"],
                "pin_token_candidate": vw["pin_token_candidate"],
                "source": vw["source"],
                "decode_score": vw["decode_score"],
            })
            raw_keys.add(key)

        references = [w for w in words if w["reference_candidate"]]
        pin_text_candidates = [w for w in words if w["pin_token_candidate"]]
        symbols = []
        terminals = []
        for ref_index, ref in enumerate(references):
            rb = ref["bbox"]
            cx, cy = (rb[0] + rb[2]) / 2, (rb[1] + rb[3]) / 2
            nearby = []
            endpoint_rows = []
            for primitive in primitives:
                if (_inside_or_near(primitive["a"], rb, symbol_context_pt)
                        or _inside_or_near(primitive["b"], rb, symbol_context_pt)
                        or _inside_or_near(
                            ((primitive["a"][0] + primitive["b"][0]) / 2,
                             (primitive["a"][1] + primitive["b"][1]) / 2),
                            rb, symbol_context_pt)):
                    nearby.append(primitive["id"])
                    for endpoint_name in ("a", "b"):
                        point = primitive[endpoint_name]
                        endpoint_rows.append({
                            "primitive_id": primitive["id"],
                            "endpoint": endpoint_name, "xy": point,
                            "distance_to_reference_text_center_pt": round(_dist(point, (cx, cy)), 4),
                        })
            # Spatially nearby printed pin tokens are retained as alternatives.
            # This is not a pin assignment: repeated/overlapping candidates stay UNKNOWN.
            pin_alternatives = []
            for token in pin_text_candidates:
                tb = token["bbox"]
                tx, ty = (tb[0] + tb[2]) / 2, (tb[1] + tb[3]) / 2
                distance = _dist((tx, ty), (cx, cy))
                if distance <= symbol_context_pt:
                    pin_alternatives.append({
                        "text": token["text"], "bbox": tb,
                        "distance_to_reference_text_center_pt": round(distance, 4),
                        "source": token["source"],
                    })
            symbols.append({
                "page": page_no, "reference_text": ref["text"],
                "reference_bbox": rb, "nearby_primitive_ids": sorted(set(nearby)),
                "printed_pin_text_alternatives": sorted(
                    pin_alternatives, key=lambda x: x["distance_to_reference_text_center_pt"]),
                "ownership": "REFERENCE_ASSOCIATION_CANDIDATE",
                "symbol_identity": "UNKNOWN",
                "pin_identity": "UNKNOWN",
                "source": "independent_pdf_reference_and_geometry_discovery",
            })
            # Keep a bounded shortlist; all raw geometry remains in the
            # primitive inventory, so candidates can be recomputed without
            # duplicating thousands of endpoints for every reference label.
            endpoint_rows.sort(key=lambda row: row["distance_to_reference_text_center_pt"])
            for endpoint in endpoint_rows[:32]:
                terminals.append({
                    "page": page_no, "reference_candidate": ref["text"],
                    **endpoint, "terminal_identity": "UNKNOWN",
                    "pin_number": None, "electrical_membership": "UNKNOWN",
                    "status": "GEOMETRIC_TERMINAL_CANDIDATE",
                })

        # A page-local geometric contact inventory. It intentionally does not
        # union components or produce nets: wire/artwork ownership is unresolved.
        endpoint_bins = defaultdict(list)
        for primitive in primitives:
            for endpoint_name in ("a", "b"):
                p = primitive[endpoint_name]
                key = (round(p[0] / endpoint_tolerance_pt),
                       round(p[1] / endpoint_tolerance_pt))
                endpoint_bins[key].append((primitive["id"], endpoint_name, p))

        page_results.append({
            "page": page_no,
            "page_size_pt": [round(media.width, 4), round(media.height, 4)],
            "text_words": words,
            "references": references,
            "symbol_candidates": symbols,
            "primitive_inventory": primitives,
            "junction_mark_candidates": dots,
            "terminal_candidates": terminals,
            "geometric_endpoint_contact_bins": [
                {"cell": list(key), "endpoints": [
                    {"primitive_id": x[0], "endpoint": x[1], "xy": x[2]}
                    for x in values
                ]}
                for key, values in endpoint_bins.items() if len(values) > 1
            ],
            "summary": {
                "text_words": len(words),
                "raw_pdf_text_words": sum(w["source"] == "pymupdf_raw_pdf_text" for w in words),
                "vector_stroke_text_words": sum(w["source"] == "raw_pdf_vector_strokes_decoded_with_kicad_newstroke" for w in words),
                "reference_candidates": len(references),
                "vector_reference_candidates": sum(w["reference_candidate"] and w["source"] == "raw_pdf_vector_strokes_decoded_with_kicad_newstroke" for w in words),
                "printed_pin_text_candidates": len(pin_text_candidates),
                "symbol_association_candidates": len(symbols),
                "vector_primitives": len(primitives),
                "wire_or_graphic_candidates": sum(
                    p["classification"] == "WIRE_OR_GRAPHIC_CANDIDATE" for p in primitives),
                "junction_mark_candidates": len(dots),
                "geometric_terminal_candidates": len(endpoint_rows),
                "geometric_terminal_candidates_retained_for_review": min(32, len(endpoint_rows)),
                "verified_symbol_ownership": 0,
                "verified_pin_identity": 0,
                "verified_wire_primitives": 0,
                "verified_electrical_memberships": 0,
            },
        })

    doc.close()
    totals = Counter()
    for result in page_results:
        totals.update(result["summary"])
    return {
        "schema": "qi9-independent-pdf-forensic-ir/v1",
        "source_pdf": str(path.name),
        "source_sha256": digest,
        "pages": pages,
        "independence_contract": {
            "reads_production_terminal_ledger": False,
            "reads_parser_pin_candidates": False,
            "discovers_reference_text_from_raw_pdf_vector_strokes": True,
            "discovers_pin_text_candidates_from_raw_pdf_vector_strokes": True,
            "reads_parser_net_ids": False,
            "reads_bom_for_pin_or_connectivity_inference": False,
        },
        "policy": {
            "reference_text_is_candidate_not_verified_symbol": True,
            "nearby_geometry_is_not_symbol_ownership": True,
            "line_geometry_is_not_wire_classification": True,
            "endpoint_contact_is_not_electrical_connectivity": True,
            "unknowns_are_never_auto_promoted": True,
        },
        "summary": dict(totals),
        "electrical_signoff": "BLOCKED_INDEPENDENT_SYMBOL_PIN_AND_WIRE_CLASSIFICATION_REQUIRED",
        "page_results": page_results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--pages", nargs="+", type=int, default=list(range(5, 12)))
    parser.add_argument("--out", required=True)
    parser.add_argument("--symbol-context-pt", type=float, default=72.0)
    args = parser.parse_args(argv)
    result = extract_independent_pdf_reconstruction(
        args.pdf, args.pages, symbol_context_pt=args.symbol_context_pt)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({
        "source_sha256": result["source_sha256"],
        "summary": result["summary"],
        "electrical_signoff": result["electrical_signoff"],
    }, indent=2))


if __name__ == "__main__":
    main()
