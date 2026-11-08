"""Independent raw-PDF pin-text inventory and conservative terminal resolver.

This reads PDF text spans directly with PyMuPDF. It does not import the production
text parser or promote proximity matches to VERIFIED. Text/geometry matches are
candidate evidence only; symbol ownership and pin numbering remain UNKNOWN unless
an independently reviewed fact already exists in the ledger.
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

PIN_TOKEN = re.compile(r"^(?:\d{1,3}|[A-Z]{1,2}\d{0,2}|[A-Z]\+|[A-Z]-)$", re.I)
REF_TOKEN = re.compile(r"^(?:RN|SW|[A-Z])\d+[A-Z]?$", re.I)
NET_OR_VALUE = re.compile(
    r"^(?:GND|VCC|VIN|VOUT|TX[A-Z0-9_-]*|RX[A-Z0-9_-]*|[+-]?\d+(?:\.\d+)?(?:k|r|m|u|n|p|v|a|f|h)|[0-9]+[kKmMrR][0-9]+)$",
    re.I,
)


def _box_distance(box, x, y):
    x0, y0, x1, y1 = box
    qx = min(max(x, x0), x1)
    qy = min(max(y, y0), y1)
    return math.hypot(x - qx, y - qy)


def extract_pdf_text_inventory(pdf_path: str, pages: list[int]) -> dict:
    digest = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()
    doc = pymupdf.open(pdf_path)
    inventory = []
    for page_no in pages:
        if page_no < 1 or page_no > len(doc):
            raise ValueError(f"page out of range: {page_no}")
        page = doc[page_no - 1]
        mb = page.mediabox
        for word in page.get_text("words"):
            token = str(word[4]).strip()
            if not token:
                continue
            inventory.append({
                "page": page_no,
                "text": token,
                "bbox": [round(word[0] - mb.x0, 4), round(word[1] - mb.y0, 4),
                         round(word[2] - mb.x0, 4), round(word[3] - mb.y0, 4)],
                "pin_token_candidate": bool(PIN_TOKEN.fullmatch(token) and not REF_TOKEN.fullmatch(token)
                                            and not NET_OR_VALUE.fullmatch(token)),
                "reference_token_candidate": bool(REF_TOKEN.fullmatch(token)),
            })
    return {
        "schema": "qi9-independent-pdf-text-inventory/v1",
        "source_sha256": digest,
        "pages": pages,
        "word_count": len(inventory),
        "pin_token_word_count": sum(x["pin_token_candidate"] for x in inventory),
        "reference_token_word_count": sum(x["reference_token_candidate"] for x in inventory),
        "words": inventory,
        "authority": "RAW_PDF_TEXT_ONLY_CANDIDATE_EVIDENCE",
    }


def resolve_ledger_candidates(ledger: dict, inventory: dict, *, radius_pt: float = 16.0,
                              ambiguity_margin_pt: float = 2.0) -> dict:
    if ledger.get("source_pdf_sha256") != inventory.get("source_sha256"):
        raise ValueError("source PDF SHA-256 mismatch between ledger and independent text inventory")
    words_by_page = defaultdict(list)
    for word in inventory["words"]:
        words_by_page[word["page"]].append(word)

    rows = []
    for entry in ledger.get("entries", []):
        xy = entry.get("terminal_xy_pdf_pt") or [None, None]
        if len(xy) != 2 or xy[0] is None or xy[1] is None:
            rows.append({"page": entry.get("page"), "ref_candidate": entry.get("ref_candidate"),
                         "status": "UNKNOWN_NO_TERMINAL_COORDINATE"})
            continue
        x, y = float(xy[0]), float(xy[1])
        page_words = words_by_page.get(entry.get("page"), [])
        pin_matches = sorted(
            ((_box_distance(w["bbox"], x, y), w) for w in page_words if w["pin_token_candidate"]),
            key=lambda item: item[0],
        )
        ref_matches = sorted(
            ((_box_distance(w["bbox"], x, y), w) for w in page_words if w["reference_token_candidate"]),
            key=lambda item: item[0],
        )
        close = [(d, w) for d, w in pin_matches if d <= radius_pt]
        selected = None
        status = "UNKNOWN_NO_NEARBY_PRINTED_PIN_TOKEN"
        if close:
            if len(close) == 1 or close[1][0] - close[0][0] >= ambiguity_margin_pt:
                selected = close[0][1]
                status = "PRINTED_PIN_TEXT_CANDIDATE"
            else:
                status = "UNKNOWN_AMBIGUOUS_PRINTED_PIN_TEXT"
        existing_status = entry.get("reference_status", "UNKNOWN")
        reviewed_evidence = entry.get("reference_evidence") or {}
        symbol_verified = existing_status == "VERIFIED" and bool(reviewed_evidence.get("symbol_identity"))
        if symbol_verified:
            status = "VERIFIED_BY_INDEPENDENT_REVIEW"
        rows.append({
            "existing_reference_status": existing_status,
            "symbol_identity_status": "VERIFIED_BY_INDEPENDENT_REVIEW" if symbol_verified else "UNKNOWN",
            "page": entry.get("page"),
            "ref_candidate": entry.get("ref_candidate"),
            "parser_pin_candidate": entry.get("pin_candidate"),
            "terminal_xy_pdf_pt": [x, y],
            "pin_text_candidate": selected["text"] if selected else None,
            "pin_text_bbox_pdf_pt": selected["bbox"] if selected else None,
            "pin_text_distance_pt": round(close[0][0], 4) if selected else None,
            "nearby_pin_text_alternatives": [
                {"text": w["text"], "distance_pt": round(d, 4)} for d, w in close[:5]
            ],
            "nearest_reference_text_candidates": [
                {"text": w["text"], "distance_pt": round(d, 4)} for d, w in ref_matches[:3]
                if d <= radius_pt * 4
            ],
            "symbol_identity": reviewed_evidence.get("symbol_identity") if symbol_verified else None,
            "pin_identity_status": status,
            "electrical_status": "NOT_VERIFIED",
            "promotion_policy": "NEVER_AUTO_PROMOTE",
        })

    counts = Counter(r.get("pin_identity_status", "UNKNOWN") for r in rows)
    already_verified = sum(e.get("reference_status") == "VERIFIED" for e in ledger.get("entries", []))
    candidate = sum(r.get("pin_identity_status") == "PRINTED_PIN_TEXT_CANDIDATE"
                     and r.get("existing_reference_status") != "VERIFIED" for r in rows)
    return {
        "schema": "qi9-independent-pin-text-resolution/v1",
        "source_sha256": inventory["source_sha256"],
        "terminal_count": len(rows),
        "independently_verified_pin_facts_from_existing_review": already_verified,
        "printed_pin_text_candidates": candidate,
        "unknown_pin_identity": sum(r.get("existing_reference_status") != "VERIFIED"
                                    and r.get("pin_identity_status") != "PRINTED_PIN_TEXT_CANDIDATE"
                                    for r in rows),
        "status_counts": dict(counts),
        "symbol_identity_resolved": sum(r.get("symbol_identity_status") == "VERIFIED_BY_INDEPENDENT_REVIEW" for r in rows),
        "electrical_memberships_verified_by_text_extraction": 0,
        "policy": "Text proximity yields candidates only. It does not establish symbol ownership, pin number, or electrical connectivity.",
        "rows": rows,
    }


def build_exception_ledger(ledger: dict, text_resolution: dict, graph_comparisons: list[dict]) -> dict:
    verified = text_resolution["independently_verified_pin_facts_from_existing_review"]
    candidate = text_resolution["printed_pin_text_candidates"]
    unknown = text_resolution["unknown_pin_identity"]
    exceptions = []
    for row in text_resolution["rows"]:
        if row.get("existing_reference_status") == "VERIFIED":
            continue
        if row.get("pin_identity_status") != "PRINTED_PIN_TEXT_CANDIDATE":
            exceptions.append({
                "page": row.get("page"), "ref_candidate": row.get("ref_candidate"),
                "reason": row.get("pin_identity_status", "UNKNOWN"),
                "electrical_status": "BLOCKED",
            })
        elif row.get("symbol_identity_status") != "VERIFIED_BY_INDEPENDENT_REVIEW":
            exceptions.append({
                "page": row.get("page"), "ref_candidate": row.get("ref_candidate"),
                "reason": "PIN_TEXT_FOUND_SYMBOL_OWNERSHIP_UNKNOWN",
                "electrical_status": "BLOCKED",
            })
    return {
        "schema": "qi9-independent-reference-exception-ledger/v1",
        "source_sha256": text_resolution["source_sha256"],
        "terminal_count": text_resolution["terminal_count"],
        "pin_identity": {
            "verified": verified, "candidate_only": candidate, "unknown": unknown,
        },
        "symbol_identity": {
            "verified": sum(r.get("symbol_identity_status") == "VERIFIED_BY_INDEPENDENT_REVIEW" for r in text_resolution["rows"]),
            "candidate_only": 0,
            "unknown": sum(r.get("symbol_identity_status") != "VERIFIED_BY_INDEPENDENT_REVIEW" for r in text_resolution["rows"]),
        },
        "electrical_connectivity": {
            "verified_memberships": int((ledger.get("summary") or {}).get("independently_reviewed_facts_applied", 0)),
            "candidate_partition_comparisons": sum((x.get("partition_comparison") or {}).get("pair_comparisons", 0) for x in graph_comparisons),
            "partition_mismatch_pairs": sum((x.get("partition_comparison") or {}).get("mismatch_pairs", 0) for x in graph_comparisons),
            "signoff": "BLOCKED",
        },
        "independent_graph": {
            "pages_compared": len(graph_comparisons),
            "geometric_matches": sum(x.get("geometry_candidates", 0) for x in graph_comparisons),
            "geometry_exceptions": sum(x.get("exceptions", 0) for x in graph_comparisons),
            "electrical_proof": "NOT_ESTABLISHED_BY_GEOMETRY",
        },
        "exception_count": len(exceptions),
        "exceptions": exceptions,
        "status": "BLOCKED_INDEPENDENT_SYMBOL_OWNERSHIP_AND_ELECTRICAL_REFERENCE_INCOMPLETE",
    }



def _point_segment_distance(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    den = dx * dx + dy * dy
    t = 0.0 if den == 0 else max(0.0, min(1.0,
        ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / den))
    qx, qy = a[0] + t * dx, a[1] + t * dy
    return math.hypot(point[0] - qx, point[1] - qy)


def build_independent_evidence_fusion(ledger: dict, vector_audit: dict,
                                      graph_dir: str | Path, *,
                                      pin_radius_pt: float = 24.0,
                                      unique_margin_pt: float = 1.5,
                                      terminal_tolerance_pt: float = 0.5) -> dict:
    """Fuse independent PDF text and geometry into a review queue, never a netlist.

    Pin-token assignment is one-to-one mutual-nearest-neighbour matching. Raw
    H/V segment adjacency and graph-component membership remain candidates:
    symbol strokes have not been classified as electrical wires. No candidate
    is promoted to VERIFIED by this function.
    """
    source_hash = ledger.get("source_pdf_sha256")
    if not source_hash or source_hash != vector_audit.get("source_sha256"):
        raise ValueError("source PDF SHA-256 mismatch between ledger and vector audit")
    if vector_audit.get("terminal_count") != len(ledger.get("entries", [])):
        raise ValueError("terminal count mismatch between ledger and vector audit")

    tokens_by_page = defaultdict(list)
    refs_by_page = defaultdict(list)
    for word in vector_audit.get("words", []):
        if word.get("pin_token_candidate"):
            tokens_by_page[word["page"]].append(word)
        if word.get("reference_token_candidate"):
            refs_by_page[word["page"]].append(word)

    def box_distance_xy(xy, box):
        return _box_distance(box, float(xy[0]), float(xy[1]))

    rows = []
    graph_summaries = []
    page_graphs = {}
    for page in sorted({int(e["page"]) for e in ledger.get("entries", [])}):
        graph_path = Path(graph_dir) / f"page-{page}.pdf-reference-graph.json"
        if not graph_path.exists():
            graph_path = Path(graph_dir) / f"page-{page}.graph.json"
        if not graph_path.exists():
            raise FileNotFoundError(f"missing independent PDF graph for page {page}: {graph_path}")
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        if graph.get("source_sha256") != source_hash:
            raise ValueError(f"source PDF SHA-256 mismatch in page {page} graph")
        page_graphs[page] = graph

    # Pair the raw-vector pin tokens and reference labels with terminals.
    # Mutual nearest-neighbour prevents one printed glyph from being reused by
    # several terminal candidates; this is still only a review proposal.
    for page in sorted(page_graphs):
        entries = [(i, e) for i, e in enumerate(ledger["entries"]) if e.get("page") == page]
        tokens = tokens_by_page.get(page, [])
        refs = refs_by_page.get(page, [])
        terminal_distances = {}
        token_best_terminal = {}
        for global_i, entry in entries:
            xy = entry.get("terminal_xy_pdf_pt")
            if not xy or len(xy) != 2:
                continue
            ranked = sorted((box_distance_xy(xy, token["bbox"]), ti)
                            for ti, token in enumerate(tokens))
            terminal_distances[global_i] = ranked
            for distance, ti in ranked:
                prior = token_best_terminal.get(ti)
                if prior is None or (distance, global_i) < prior:
                    token_best_terminal[ti] = (distance, global_i)

        graph = page_graphs[page]
        segment_component = {}
        for component_id, segment_ids in enumerate(graph.get("components", [])):
            for segment_id in segment_ids:
                segment_component[int(segment_id)] = component_id
        segments = graph.get("segments", [])
        terminal_segment = {}
        for global_i, entry in entries:
            xy = entry.get("terminal_xy_pdf_pt")
            if not xy or len(xy) != 2 or not segments:
                continue
            ranked_segments = sorted(
                (_point_segment_distance(xy, s["a"], s["b"]), int(s["id"]))
                for s in segments
            )
            if ranked_segments and ranked_segments[0][0] <= terminal_tolerance_pt:
                terminal_segment[global_i] = {
                    "segment_id": ranked_segments[0][1],
                    "distance_pt": round(ranked_segments[0][0], 4),
                    "component_id": segment_component.get(ranked_segments[0][1]),
                }

        page_rows = []
        for global_i, entry in entries:
            xy = entry.get("terminal_xy_pdf_pt")
            ranked = terminal_distances.get(global_i, [])
            nearest = ranked[0] if ranked else None
            second_distance = ranked[1][0] if len(ranked) > 1 else None
            selected_token = None
            token_status = "UNKNOWN_NO_PIN_TOKEN_WITHIN_RADIUS"
            if nearest and nearest[0] <= pin_radius_pt:
                token_i = nearest[1]
                mutual = token_best_terminal.get(token_i, (None, None))[1] == global_i
                margin = (second_distance - nearest[0]) if second_distance is not None else float("inf")
                if mutual and margin >= unique_margin_pt:
                    selected_token = tokens[token_i]
                    token_status = "MUTUAL_NEAREST_PIN_TEXT_CANDIDATE"
                elif mutual:
                    token_status = "AMBIGUOUS_NEAR_TIE"
                else:
                    token_status = "TOKEN_CLOSER_TO_ANOTHER_TERMINAL"

            ref_token = None
            exact_refs = [r for r in refs if r.get("text") == entry.get("ref_candidate")]
            if len(exact_refs) == 1:
                ref_token = exact_refs[0]

            segment = terminal_segment.get(global_i)
            row = {
                "entry_index": global_i, "page": page,
                "ref_candidate": entry.get("ref_candidate"),
                "existing_reference_status": entry.get("reference_status", "UNKNOWN"),
                "terminal_xy_pdf_pt": xy,
                "symbol_ownership_candidate": (
                    "EXACT_REFERENCE_TEXT_FOUND" if ref_token else "UNKNOWN_REFERENCE_TEXT_ASSOCIATION"),
                "reference_text_bbox": ref_token.get("bbox") if ref_token else None,
                "symbol_ownership": "UNKNOWN",
                "pin_text_candidate": selected_token.get("text") if selected_token else None,
                "pin_text_bbox": selected_token.get("bbox") if selected_token else None,
                "pin_text_status": token_status,
                "pin_identity": "UNKNOWN",
                "adjacent_segment_candidate": segment,
                "wire_classification": "UNKNOWN",
                "graph_component_candidate": segment.get("component_id") if segment else None,
                "net_membership": "UNKNOWN",
                "parser_net_candidate_diagnostic_only": entry.get("candidate_net_name_diagnostic_only"),
                "review_status": "REQUIRES_SOURCE_CROP_REVIEW",
                "promotion_policy": "NEVER_AUTO_PROMOTE",
            }
            rows.append(row)
            page_rows.append(row)

        terminal_points = [e.get("terminal_xy_pdf_pt") for _, e in entries
                           if e.get("terminal_xy_pdf_pt") and len(e["terminal_xy_pdf_pt"]) == 2]
        adjacent_segments = 0
        for segment in segments:
            if any(_point_segment_distance(p, segment["a"], segment["b"]) <= terminal_tolerance_pt
                   for p in terminal_points):
                adjacent_segments += 1
        graph_summaries.append({
            "page": page, "raw_hv_segment_count": len(segments),
            "terminal_adjacent_segment_candidates": adjacent_segments,
            "terminal_segment_candidates": sum(1 for r in page_rows if r["adjacent_segment_candidate"]),
            "symbol_wire_classification_verified": 0,
        })

    # Compare pairwise partitions only where both independent graph component
    # candidates and parser net-name candidates exist. Agreement is diagnostic,
    # never proof, because the graph still includes unclassified symbol strokes.
    pair_comparisons = agreement_pairs = mismatch_pairs = 0
    comparable_by_page = defaultdict(list)
    for row in rows:
        if row["graph_component_candidate"] is not None and row["parser_net_candidate_diagnostic_only"]:
            comparable_by_page[row["page"]].append(row)
    for page_rows in comparable_by_page.values():
        for i, left in enumerate(page_rows):
            for right in page_rows[i + 1:]:
                same_parser = left["parser_net_candidate_diagnostic_only"] == right["parser_net_candidate_diagnostic_only"]
                same_graph = left["graph_component_candidate"] == right["graph_component_candidate"]
                pair_comparisons += 1
                if same_parser == same_graph:
                    agreement_pairs += 1
                else:
                    mismatch_pairs += 1

    pin_candidate_count = sum(r["pin_text_status"] == "MUTUAL_NEAREST_PIN_TEXT_CANDIDATE" for r in rows)
    reference_candidate_count = sum(r["symbol_ownership_candidate"] == "EXACT_REFERENCE_TEXT_FOUND" for r in rows)
    terminal_segment_count = sum(r["adjacent_segment_candidate"] is not None for r in rows)
    verified_existing = sum(e.get("reference_status") == "VERIFIED" for e in ledger.get("entries", []))
    return {
        "schema": "qi9-independent-evidence-fusion-review/v1",
        "source_sha256": source_hash,
        "terminal_count": len(rows),
        "stages": {
            "wire_vs_symbol_vs_graphic": {
                "raw_hv_segments": sum(x["raw_hv_segment_count"] for x in graph_summaries),
                "terminal_adjacent_segment_candidates": sum(x["terminal_adjacent_segment_candidates"] for x in graph_summaries),
                "verified_wire_classifications": 0,
                "status": "CANDIDATE_ONLY_UNCLASSIFIED_STROKES",
            },
            "symbol_ownership": {
                "exact_reference_text_candidates": reference_candidate_count,
                "independently_verified_existing_facts": verified_existing,
                "newly_verified": 0,
                "status": "CANDIDATE_ONLY",
            },
            "pin_identity": {
                "mutual_nearest_one_to_one_text_candidates": pin_candidate_count,
                "unknown_or_ambiguous": sum(r["pin_text_status"] != "MUTUAL_NEAREST_PIN_TEXT_CANDIDATE"
                                            and r["existing_reference_status"] != "VERIFIED" for r in rows),
                "newly_verified": 0,
                "status": "CANDIDATE_ONLY",
            },
            "net_membership": {
                "terminal_segment_component_candidates": terminal_segment_count,
                "pair_comparisons": pair_comparisons,
                "partition_agreement_pairs": agreement_pairs,
                "partition_mismatch_pairs": mismatch_pairs,
                "newly_verified_memberships": 0,
                "status": "DIAGNOSTIC_ONLY",
            },
        },
        "page_summaries": graph_summaries,
        "review_rows": rows,
        "policy": {
            "one_printed_token_is_never_reused_for_multiple_terminals": True,
            "raw_hv_geometry_is_not_assumed_to_be_wire": True,
            "reference_text_proximity_is_not_symbol_ownership": True,
            "pin_text_match_is_not_pin_identity": True,
            "graph_component_is_not_verified_net_membership": True,
            "no_candidate_is_auto_promoted": True,
        },
        "electrical_signoff": "BLOCKED",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--graph-dir", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--pages", nargs="+", type=int, default=list(range(5, 12)))
    ap.add_argument("--vector-audit")
    ap.add_argument("--fused-out")
    args = ap.parse_args(argv)
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    ledger = json.loads(Path(args.ledger).read_text(encoding="utf-8"))
    inventory = extract_pdf_text_inventory(args.pdf, args.pages)
    resolution = resolve_ledger_candidates(ledger, inventory)
    comparisons = []
    for page in args.pages:
        path = Path(args.graph_dir) / f"page-{page}.graph-comparison.json"
        if path.exists():
            comparisons.append(json.loads(path.read_text(encoding="utf-8")))
    exceptions = build_exception_ledger(ledger, resolution, comparisons)
    if args.fused_out:
        if not args.vector_audit or not args.graph_dir:
            raise SystemExit("--fused-out requires --vector-audit and --graph-dir")
        vector_audit = json.loads(Path(args.vector_audit).read_text(encoding="utf-8"))
        fused = build_independent_evidence_fusion(ledger, vector_audit, args.graph_dir)
        Path(args.fused_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.fused_out).write_text(json.dumps(fused, indent=2), encoding="utf-8")
    (out / "independent_pdf_text_inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    (out / "independent_pin_text_resolution.json").write_text(json.dumps(resolution, indent=2), encoding="utf-8")
    (out / "independent_reference_exception_ledger.json").write_text(json.dumps(exceptions, indent=2), encoding="utf-8")
    print(json.dumps({
        "terminals": resolution["terminal_count"],
        "verified_pin_facts": resolution["independently_verified_pin_facts_from_existing_review"],
        "printed_pin_text_candidates": resolution["printed_pin_text_candidates"],
        "unknown_pin_identity": resolution["unknown_pin_identity"],
        "independent_graph_pages": exceptions["independent_graph"]["pages_compared"],
        "electrical_signoff": "BLOCKED",
        "fused_review_rows": len(fused["review_rows"]) if args.fused_out else 0,
        "mutual_nearest_pin_text_candidates": fused["stages"]["pin_identity"]["mutual_nearest_one_to_one_text_candidates"] if args.fused_out else 0,
        "partition_mismatch_pairs": fused["stages"]["net_membership"]["partition_mismatch_pairs"] if args.fused_out else 0,
        "status": exceptions["status"],
    }, indent=2))


if __name__ == "__main__":
    main()
