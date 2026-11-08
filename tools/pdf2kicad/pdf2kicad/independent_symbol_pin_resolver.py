"""Ledger-independent geometric resolver candidates for PDF schematic symbols/pins.

Coincident endpoints from adjacent PDF drawing primitives are one physical point.
They are collapsed before ambiguity scoring; otherwise a single wire corner is
incorrectly counted as multiple competing endpoints. This remains candidate-only:
geometry does not prove symbol ownership, pin number, or electrical connectivity.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _physical_endpoints(primitives, *, merge_tolerance_pt=0.05):
    """Collapse duplicate endpoint coordinates while preserving primitive provenance."""
    cells = defaultdict(list)
    for primitive in primitives:
        for side in ("a", "b"):
            point = primitive[side]
            key = (round(point[0] / merge_tolerance_pt),
                   round(point[1] / merge_tolerance_pt))
            cells[key].append({
                "primitive_id": primitive["id"], "side": side,
                "xy": [float(point[0]), float(point[1])],
            })
    endpoints = []
    for key, members in sorted(cells.items()):
        # Use the mean only for numerically coincident coordinates; never move
        # endpoints across the configured merge tolerance.
        xs = [m["xy"][0] for m in members]
        ys = [m["xy"][1] for m in members]
        point = [sum(xs) / len(xs), sum(ys) / len(ys)]
        if any(_distance(point, m["xy"]) > merge_tolerance_pt for m in members):
            # Quantization-cell collision: retain each source point independently.
            for member in members:
                endpoints.append({
                    "xy": member["xy"], "primitive_ids": [member["primitive_id"]],
                    "source_sides": [member["side"]],
                })
            continue
        endpoints.append({
            "xy": [round(point[0], 4), round(point[1], 4)],
            "primitive_ids": sorted({m["primitive_id"] for m in members}),
            "source_sides": sorted({m["side"] for m in members}),
        })
    return endpoints


def resolve_page(page_result: dict, *, endpoint_radius_pt: float = 4.0,
                 unique_margin_pt: float = 1.0,
                 endpoint_merge_tolerance_pt: float = 0.05) -> dict:
    """Associate pin-text with distinct physical endpoint candidates.

    Uniqueness is measured between distinct endpoint coordinates, not duplicated
    primitive records. A unique geometric attachment remains CANDIDATE_ONLY.
    """
    if endpoint_radius_pt < 0 or unique_margin_pt < 0 or endpoint_merge_tolerance_pt <= 0:
        raise ValueError("radii/margins must be non-negative and merge tolerance positive")
    endpoints = _physical_endpoints(
        page_result.get("primitive_inventory", []),
        merge_tolerance_pt=endpoint_merge_tolerance_pt,
    )
    pin_words = [w for w in page_result.get("text_words", [])
                 if w.get("pin_token_candidate")]
    rows = []
    for word in pin_words:
        point = _center(word["bbox"])
        ranked = sorted(
            ((_distance(point, endpoint["xy"]), endpoint) for endpoint in endpoints),
            key=lambda item: (item[0], item[1]["xy"]),
        )
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
                "primitive_ids": best[1]["primitive_ids"],
                "primitive_id": (best[1]["primitive_ids"][0] if len(best[1]["primitive_ids"]) == 1 else None),
                "source_sides": best[1]["source_sides"],
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
        "raw_endpoint_record_count": sum(2 for _ in page_result.get("primitive_inventory", [])),
        "distinct_physical_endpoint_count": len(endpoints),
        "pin_text_candidates_with_unique_endpoint_attachment": counts["UNIQUE_ENDPOINT_ATTACHMENT_CANDIDATE"],
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
                     unique_margin_pt: float = 1.0,
                     endpoint_merge_tolerance_pt: float = 0.05) -> dict:
    pages = [resolve_page(
        p, endpoint_radius_pt=endpoint_radius_pt,
        unique_margin_pt=unique_margin_pt,
        endpoint_merge_tolerance_pt=endpoint_merge_tolerance_pt,
    ) for p in ir.get("page_results", [])]
    totals = Counter()
    for page in pages:
        for key in (
            "pin_text_candidate_count", "unique_endpoint_attachment_candidates",
            "ambiguous_endpoint_attachment_candidates", "without_near_endpoint",
            "raw_endpoint_record_count", "distinct_physical_endpoint_count",
        ):
            totals[key] += page[key]
    return {
        "schema": "qi9-independent-symbol-pin-resolver-candidates/v2",
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
            "coincident_primitive_endpoints_collapsed": True,
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
    ap.add_argument("--endpoint-merge-tolerance-pt", type=float, default=0.05)
    args = ap.parse_args(argv)
    ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    result = resolve_document(
        ir, endpoint_radius_pt=args.endpoint_radius_pt,
        unique_margin_pt=args.unique_margin_pt,
        endpoint_merge_tolerance_pt=args.endpoint_merge_tolerance_pt,
    )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": result["summary"],
                      "electrical_signoff": result["electrical_signoff"]}, indent=2))


if __name__ == "__main__":
    main()
