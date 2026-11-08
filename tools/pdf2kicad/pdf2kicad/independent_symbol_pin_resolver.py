"""Ledger-independent geometric resolver candidates for PDF schematic symbols/pins.

This module performs conservative spatial association only. A nearby pin token is
not a pin identity, and a nearby drawing component is not symbol ownership.
No result is promoted to VERIFIED and no electrical net is emitted.
"""
from __future__ import annotations

import math
from collections import Counter


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _point_segment_distance(point, a, b):
    px, py = point
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(
        0.0, min(1.0, ((px-a[0])*dx + (py-a[1])*dy) / length2))
    return math.hypot(px - (a[0] + t*dx), py - (a[1] + t*dy))


def resolve_page(page_result: dict, *, endpoint_radius_pt: float = 4.0,
                 unique_margin_pt: float = 1.0) -> dict:
    """Associate printed pin-text candidates with nearest raw-PDF geometry endpoints.

    A candidate is called unique only when it is within the radius and the next
    endpoint is farther by the configured margin. This is still CANDIDATE_ONLY:
    symbol ownership and library pin identity remain UNKNOWN.
    """
    endpoints = []
    for primitive in page_result.get("primitive_inventory", []):
        for side in ("a", "b"):
            endpoints.append({
                "primitive_id": primitive["id"],
                "side": side,
                "xy": primitive[side],
                "axis": primitive.get("axis", "UNKNOWN"),
                "length_pt": primitive.get("length_pt"),
            })
    pin_words = [w for w in page_result.get("text_words", [])
                 if w.get("pin_token_candidate")]
    rows = []
    for word in pin_words:
        point = _center(word["bbox"])
        ranked = sorted(
            ((_point_segment_distance(point, e["xy"], e["xy"]), e)
             for e in endpoints),
            key=lambda x: x[0])
        # Endpoint distance, rather than distance to the supporting segment, is
        # deliberate: this tests attachment to a physical end, not a wire crossing.
        best = ranked[0] if ranked else None
        second_distance = ranked[1][0] if len(ranked) > 1 else float("inf")
        if best is None or best[0] > endpoint_radius_pt:
            decision = "NO_NEAR_ENDPOINT"
        elif second_distance - best[0] >= unique_margin_pt:
            decision = "UNIQUE_ENDPOINT_ATTACHMENT_CANDIDATE"
        else:
            decision = "AMBIGUOUS_ENDPOINT_ATTACHMENT"
        rows.append({
            "page": page_result["page"],
            "token": word["text"],
            "token_bbox": word["bbox"],
            "token_source": word.get("source"),
            "nearest_endpoint": (None if best is None else {
                "primitive_id": best[1]["primitive_id"],
                "side": best[1]["side"],
                "xy": best[1]["xy"],
                "distance_pt": round(best[0], 4),
            }),
            "second_nearest_distance_pt": (
                None if not math.isfinite(second_distance) else round(second_distance, 4)),
            "decision": decision,
            "symbol_ownership": "UNKNOWN",
            "library_pin_identity": "UNKNOWN",
            "electrical_membership": "UNKNOWN",
            "authority": "CANDIDATE_ONLY_NEVER_AUTO_PROMOTE",
        })
    counts = Counter(row["decision"] for row in rows)
    return {
        "page": page_result["page"],
        "pin_text_candidate_count": len(rows),
        "endpoint_count": len(endpoints),
        "unique_endpoint_attachment_candidates": counts["UNIQUE_ENDPOINT_ATTACHMENT_CANDIDATE"],
        "ambiguous_endpoint_attachment_candidates": counts["AMBIGUOUS_ENDPOINT_ATTACHMENT"],
        "without_near_endpoint": counts["NO_NEAR_ENDPOINT"],
        "verified_symbol_ownership": 0,
        "verified_pin_identity": 0,
        "verified_electrical_memberships": 0,
        "candidates": rows,
        "status": "GEOMETRIC_PIN_TEXT_ASSOCIATION_CANDIDATES_ONLY",
    }


def resolve_document(ir: dict, *, endpoint_radius_pt: float = 4.0,
                     unique_margin_pt: float = 1.0) -> dict:
    pages = [resolve_page(p, endpoint_radius_pt=endpoint_radius_pt,
                          unique_margin_pt=unique_margin_pt)
             for p in ir.get("page_results", [])]
    totals = Counter()
    for page in pages:
        totals.update({
            "pin_text_candidate_count": page["pin_text_candidate_count"],
            "unique_endpoint_attachment_candidates": page["unique_endpoint_attachment_candidates"],
            "ambiguous_endpoint_attachment_candidates": page["ambiguous_endpoint_attachment_candidates"],
            "without_near_endpoint": page["without_near_endpoint"],
            "endpoint_count": page["endpoint_count"],
        })
    return {
        "schema": "qi9-independent-symbol-pin-resolver-candidates/v1",
        "source_sha256": ir.get("source_sha256"),
        "pages": pages,
        "summary": dict(totals),
        "verified_symbol_ownership": 0,
        "verified_pin_identity": 0,
        "verified_electrical_memberships": 0,
        "electrical_signoff": "BLOCKED_SYMBOL_OWNERSHIP_PIN_IDENTITY_AND_WIRE_CLASSIFICATION",
        "policy": {
            "raw_pdf_geometry_only": True,
            "production_terminal_ledger_read": False,
            "parser_pin_candidates_read": False,
            "pin_text_proximity_is_not_pin_identity": True,
            "endpoint_attachment_is_candidate_only": True,
            "no_auto_promotion": True,
        },
    }

def main(argv=None):
    import argparse
    import json
    from pathlib import Path
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--endpoint-radius-pt", type=float, default=4.0)
    ap.add_argument("--unique-margin-pt", type=float, default=1.0)
    args = ap.parse_args(argv)
    ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    result = resolve_document(ir, endpoint_radius_pt=args.endpoint_radius_pt,
                              unique_margin_pt=args.unique_margin_pt)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\\n", encoding="utf-8")
    print(json.dumps({"summary": result["summary"],
                      "electrical_signoff": result["electrical_signoff"]}, indent=2))


if __name__ == "__main__":
    main()
