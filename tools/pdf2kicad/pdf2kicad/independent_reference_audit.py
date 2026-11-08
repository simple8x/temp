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
        rows.append({
            "existing_reference_status": entry.get("reference_status", "UNKNOWN"),
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
            "symbol_identity": "UNKNOWN_NO_INDEPENDENT_SYMBOL_BODY_OWNERSHIP",
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
        "symbol_identity_resolved": 0,
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
        elif row.get("symbol_identity") != "RESOLVED":
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
        "symbol_identity": {"verified": 0, "candidate_only": 0, "unknown": text_resolution["terminal_count"]},
        "electrical_connectivity": {
            "verified_memberships": int((ledger.get("summary") or {}).get("independently_reviewed_facts_applied", 0)),
            "candidate_partition_comparisons": sum(x.get("parser_rows", 0) for x in graph_comparisons),
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--graph-dir", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--pages", nargs="+", type=int, default=list(range(5, 12)))
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
        "status": exceptions["status"],
    }, indent=2))


if __name__ == "__main__":
    main()
