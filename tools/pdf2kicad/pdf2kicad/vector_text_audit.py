"""Independent inventory of KiCad vector-stroke text near schematic terminals.

This reads raw PDF drawing strokes, decodes glyph shapes against the bundled
KiCad newstroke font, and reports nearby pin/ref-like tokens as CANDIDATES.
It never assigns a symbol, pin number, net, or VERIFIED status.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from pdf2kicad.primitives import load_page
from pdf2kicad import glyphs as G, font, textdecode as T

# Classify reference designators before pin numbers. The former permissive
# [A-Z]{1,2}\d{0,2} pattern misclassified C5/U3/B12 and package/value text
# such as SS34 as pin labels. In this source, a printed pin identifier is
# accepted only when it is a 1- or 2-digit pin number or a single-letter
# electrical pin name (optionally +/-). Alphanumeric strings are deliberately
# left UNKNOWN for review; proximity cannot disambiguate them safely.
PIN_TOKEN = re.compile(r"^(?:\d{1,2}|[A-Z](?:[+-])?)$", re.I)
REF_TOKEN = re.compile(r"^(?:RN|SW|[A-Z])\d+[A-Z]?$", re.I)


def is_pin_token_candidate(text: str) -> bool:
    token = text.strip()
    return bool(token and not REF_TOKEN.fullmatch(token)
                and PIN_TOKEN.fullmatch(token))


def box_distance(point, bbox):
    x, y = point
    x0, y0, x1, y1 = bbox
    dx = max(x0 - x, 0.0, x - x1)
    dy = max(y0 - y, 0.0, y - y1)
    return (dx * dx + dy * dy) ** 0.5


def decode_vector_words(pdf: str, page_no: int) -> list[dict]:
    page = load_page(pdf, page_no - 1, 1.0, 0)
    glyphs = G.find_glyphs(page.segs)
    words = G.build_words(glyphs)
    templates = font.load_templates()
    T.decode_all(words, templates, G.estimate_text_sizes(words))
    out = []
    for word in words:
        if not T.is_text(word) or "?" in word.text or word.score > 0.04:
            continue
        text = word.text.strip()
        if not text:
            continue
        out.append({
            "page": page_no, "text": text,
            "bbox": [round(word.x0, 4), round(word.y0, 4),
                     round(word.x1, 4), round(word.y1, 4)],
            "rotation": word.rot, "decode_score": round(float(word.score), 6),
            "pin_token_candidate": is_pin_token_candidate(text),
            "reference_token_candidate": bool(REF_TOKEN.fullmatch(text)),
            "token_classification": (
                "REFERENCE_DESIGNATOR" if REF_TOKEN.fullmatch(text)
                else "PIN_TOKEN_CANDIDATE" if is_pin_token_candidate(text)
                else "OTHER_OR_AMBIGUOUS"
            ),
            "source": "raw_pdf_vector_strokes_decoded_with_kicad_newstroke",
        })
    return out


def audit(pdf: str, ledger_path: str, pages: list[int], radius: float = 24.0) -> dict:
    ledger = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    vector_words = [w for p in pages for w in decode_vector_words(pdf, p)]
    terminals = [e for e in ledger.get("entries", []) if e.get("page") in pages]
    rows = []
    for terminal in terminals:
        xy = terminal.get("terminal_xy_pdf_pt") or []
        if len(xy) != 2:
            continue
        pin_words = [w for w in vector_words if w["page"] == terminal["page"]
                     and w["pin_token_candidate"]]
        ref_words = [w for w in vector_words if w["page"] == terminal["page"]
                     and w["reference_token_candidate"]]
        near_pin = sorted(
            ((box_distance(xy, w["bbox"]), w) for w in pin_words),
            key=lambda item: item[0])
        near_ref = sorted(
            ((box_distance(xy, w["bbox"]), w) for w in ref_words),
            key=lambda item: item[0])
        selected = near_pin[0] if near_pin and near_pin[0][0] <= radius else None
        rows.append({
            "page": terminal["page"], "terminal_xy_pdf_pt": xy,
            "ref_candidate": terminal.get("ref_candidate"),
            "pin_candidate_from_existing_parser": terminal.get("pin_candidate"),
            "nearest_vector_pin_token": selected[1]["text"] if selected else None,
            "nearest_vector_pin_distance_pt": round(selected[0], 4) if selected else None,
            "nearby_vector_pin_alternatives": [
                {"text": w["text"], "distance_pt": round(d, 4)}
                for d, w in near_pin[:5] if d <= radius * 2],
            "nearby_vector_reference_alternatives": [
                {"text": w["text"], "distance_pt": round(d, 4)}
                for d, w in near_ref[:5] if d <= radius * 3],
            "decision": "VECTOR_TEXT_CANDIDATE_ONLY" if selected else "UNKNOWN",
            "symbol_ownership": "UNKNOWN",
            "pin_identity": "UNKNOWN",
            "net_membership": "UNKNOWN",
            "promotion_policy": "NEVER_AUTO_PROMOTE",
        })
    candidate_rows = [r for r in rows if r["decision"] == "VECTOR_TEXT_CANDIDATE_ONLY"]
    return {
        "schema": "qi9-independent-vector-text-audit/v2",
        "source_sha256": hashlib.sha256(Path(pdf).read_bytes()).hexdigest(),
        "pages": pages, "vector_text_word_count": len(vector_words),
        "vector_pin_token_count": sum(w["pin_token_candidate"] for w in vector_words),
        "vector_reference_token_count": sum(w["reference_token_candidate"] for w in vector_words),
        "vector_other_or_ambiguous_token_count": sum(w["token_classification"] == "OTHER_OR_AMBIGUOUS" for w in vector_words),
        "terminal_count": len(rows),
        "nearby_vector_pin_candidates": len(candidate_rows),
        "unknown_terminals": len(rows) - len(candidate_rows),
        "electrical_memberships_verified": 0,
        "words": vector_words, "terminal_candidates": rows,
        "authority": "Strict token shape and proximity produce candidates only; no electrical or symbol identity is verified.",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pages", nargs="+", type=int, default=list(range(5, 12)))
    ap.add_argument("--radius", type=float, default=24.0)
    args = ap.parse_args(argv)
    result = audit(args.pdf, args.ledger, args.pages, args.radius)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in (
        "vector_text_word_count", "vector_pin_token_count",
        "vector_reference_token_count", "vector_other_or_ambiguous_token_count",
        "terminal_count", "nearby_vector_pin_candidates", "unknown_terminals",
        "electrical_memberships_verified")}, indent=2))


if __name__ == "__main__":
    main()
