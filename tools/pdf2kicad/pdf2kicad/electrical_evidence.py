"""Evidence-backed detection of explicit filled junction marks in KiCad vector PDFs.

A mark is promoted only when a small filled circular vector path is present and
at least three distinct orthogonal wire-like line rays terminate at its centre.
Bare geometric crossings are never joined by this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import fitz


def _black(color):
    return color is not None and len(color) >= 3 and all(float(v) <= 0.08 for v in color[:3])


def _candidate_mark(drawing):
    rect = drawing.get("rect")
    fill = drawing.get("fill")
    items = drawing.get("items", [])
    if rect is None or not _black(fill) or len(items) != 4:
        return None
    if not all(item[0] == "c" for item in items):
        return None
    w, h = abs(float(rect.width)), abs(float(rect.height))
    if not (1.5 <= w <= 3.5 and 1.5 <= h <= 3.5):
        return None
    if abs(w - h) > 0.35:
        return None
    stroke_width = float(drawing.get("width") or 0.0)
    if not (0.35 <= stroke_width <= 0.85):
        return None
    return ((float(rect.x0) + float(rect.x1)) / 2,
            (float(rect.y0) + float(rect.y1)) / 2)


def _line_segments(drawings):
    result = []
    for di, drawing in enumerate(drawings):
        width = float(drawing.get("width") or 0.0)
        color = drawing.get("color")
        if abs(width - 0.48) > 0.03 or not _black(color):
            continue
        for ii, item in enumerate(drawing.get("items", [])):
            if item[0] != "l":
                continue
            a, b = item[1], item[2]
            ax, ay, bx, by = float(a.x), float(a.y), float(b.x), float(b.y)
            dx, dy = bx - ax, by - ay
            if math.hypot(dx, dy) < 1.0:
                continue
            if min(abs(dx), abs(dy)) > max(abs(dx), abs(dy)) * 0.01:
                continue
            result.append({
                "id": f"d{di}-i{ii}",
                "a": [round(ax, 4), round(ay, 4)],
                "b": [round(bx, 4), round(by, 4)],
                "width_pt": round(width, 4),
            })
    return result


def _incident_rays(center, lines, tolerance=0.15):
    rays = {}
    for line in lines:
        a, b = line["a"], line["b"]
        for endpoint, other in ((a, b), (b, a)):
            if math.hypot(center[0] - endpoint[0], center[1] - endpoint[1]) > tolerance:
                continue
            dx, dy = other[0] - center[0], other[1] - center[1]
            if abs(dx) > abs(dy) * 10:
                direction = "E" if dx > 0 else "W"
            elif abs(dy) > abs(dx) * 10:
                direction = "S" if dy > 0 else "N"
            else:
                continue
            # Multiple coincident strokes in one ray do not inflate the branch count.
            rays.setdefault(direction, []).append({
                "segment_id": line["id"],
                "endpoint": endpoint,
                "other_endpoint": other,
            })
    return rays


def classify_page(pdf_path: str | Path, page_number: int, tolerance: float = 0.15):
    """Classify explicit junction marks on a 1-based PDF page number."""
    pdf_path = Path(pdf_path)
    raw = pdf_path.read_bytes()
    doc = fitz.open(stream=raw, filetype="pdf")
    if page_number < 1 or page_number > len(doc):
        raise ValueError(f"page {page_number} outside 1..{len(doc)}")
    page = doc[page_number - 1]
    drawings = page.get_drawings()
    lines = _line_segments(drawings)
    candidates = []
    for di, drawing in enumerate(drawings):
        center = _candidate_mark(drawing)
        if center is None:
            continue
        rays = _incident_rays(center, lines, tolerance=tolerance)
        directions = sorted(rays)
        confirmed = len(directions) >= 3
        candidates.append({
            "mark_id": f"p{page_number}-d{di}",
            "drawing_index": di,
            "center_pt": [round(center[0], 4), round(center[1], 4)],
            "bbox_pt": [round(float(v), 4) for v in drawing["rect"]],
            "stroke_width_pt": round(float(drawing.get("width") or 0.0), 4),
            "incident_directions": directions,
            "incident_segments": [entry for direction in directions for entry in rays[direction]],
            "status": "EXPLICIT_JUNCTION_CONFIRMED" if confirmed else "MARK_CANDIDATE_UNRESOLVED",
            "evidence": [
                "filled_black_four_curve_circular_mark",
                "orthogonal_black_0.48pt_line_endpoints_at_mark_center",
                f"{len(directions)}_distinct_incident_directions",
            ] if confirmed else [
                "filled_black_four_curve_circular_mark",
                f"only_{len(directions)}_distinct_incident_directions",
            ],
        })
    confirmed = [x for x in candidates if x["status"] == "EXPLICIT_JUNCTION_CONFIRMED"]
    return {
        "schema": "qi9-pdf-explicit-junction-evidence/v1",
        "source_pdf": pdf_path.name,
        "source_pdf_sha256": hashlib.sha256(raw).hexdigest(),
        "page": page_number,
        "page_dimensions_pt": [round(page.rect.width, 4), round(page.rect.height, 4)],
        "summary": {
            "line_primitives_considered": len(lines),
            "filled_circle_mark_candidates": len(candidates),
            "explicit_junctions_confirmed": len(confirmed),
            "unresolved_mark_candidates": len(candidates) - len(confirmed),
            "verified_net_memberships": 0,
            "status": "JUNCTION_MARKS_VERIFIED_ONLY_NETLIST_STILL_REQUIRES_FULL_TOPOLOGY",
        },
        "policy": {
            "bare_geometric_crossings": "UNJOINED",
            "join_rule": "filled circular mark plus >=3 distinct orthogonal line rays ending at its centre",
            "unresolved_marks": "remain candidates; never promoted",
            "scope": "junction evidence only; does not infer component pin identity or net membership",
        },
        "junction_candidates": candidates,
    }


def classify_pages(pdf_path: str | Path, pages=range(5, 12), outdir: str | Path | None = None):
    results = [classify_page(pdf_path, page) for page in pages]
    if outdir is not None:
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        for result in results:
            path = outdir / f"page{result['page']:02d}_junction_evidence.json"
            path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf")
    parser.add_argument("--pages", default="5-11", help="inclusive page range, e.g. 5-11")
    parser.add_argument("--outdir", default="junction_evidence")
    args = parser.parse_args(argv)
    start, end = (int(x) for x in args.pages.split("-", 1))
    results = classify_pages(args.pdf, range(start, end + 1), args.outdir)
    print(json.dumps([{"page": r["page"], **r["summary"]} for r in results], indent=2))


if __name__ == "__main__":
    main()
