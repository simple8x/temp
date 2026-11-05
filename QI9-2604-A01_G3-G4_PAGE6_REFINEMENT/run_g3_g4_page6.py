#!/usr/bin/env python3
"""Evidence-only G3/G4 extraction for one PDF page; never emits a netlist."""
import argparse, hashlib, json, re
from collections import Counter
from pathlib import Path
import fitz
import pytesseract
from PIL import Image
from pytesseract import Output
from g3_g4_geometry import Segment, analyze_connectivity

REF = re.compile(r"^(?:[RCDQLJUFY]|SW|RN|U|Q)\d+[A-Z]?$", re.I)
NET = re.compile(r"^(?:\+?\d+(?:\.\d+)?V(?:_[A-Z0-9]+)?|GND(?:_[A-Z0-9]+)?|SHIELD_[A-Z0-9]+|UART_[A-Z0-9]+|ESP32_[A-Z0-9]+|RX[PN]?|TX[PN]?)$", re.I)

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--reference", required=True, type=Path)
    ap.add_argument("--page", type=int, default=6, help="1-based PDF page")
    ap.add_argument("--scale", type=float, default=3.0)
    ap.add_argument("--cell-size", type=float, default=4.0)
    ap.add_argument("--output", type=Path, default=Path("G3_G4_PAGE6_RUN_RESULT.json"))
    a = ap.parse_args()

    source_hash = sha256(a.pdf)
    ref = json.loads(a.reference.read_text(encoding="utf-8"))
    if ref.get("source_sha256") != source_hash:
        raise SystemExit("BLOCKED: reference artifact SHA-256 does not match source PDF")
    doc = fitz.open(a.pdf)
    if not 1 <= a.page <= len(doc):
        raise SystemExit("BLOCKED: page index outside source PDF")
    page = doc[a.page - 1]
    pix = page.get_pixmap(matrix=fitz.Matrix(a.scale, a.scale), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    ocr = pytesseract.image_to_data(img, output_type=Output.DICT, config="--psm 11")
    tokens = [s.strip() for s in ocr["text"] if s.strip()]
    refs = [s for s in tokens if REF.fullmatch(s.strip(",;:"))]
    nets = [s for s in tokens if NET.fullmatch(s.strip(",;:"))]

    segments, line_count = [], 0
    for di, drawing in enumerate(page.get_drawings()):
        for ii, item in enumerate(drawing["items"]):
            if item[0] != "l":
                continue
            line_count += 1
            p, q = item[1], item[2]
            segments.append(Segment(
                f"p{a.page}-d{di}-i{ii}", a.page,
                (float(p.x), float(p.y)), (float(q.x), float(q.y)),
                role="UNKNOWN", role_evidence="NONE"
            ))
    geom = analyze_connectivity(segments, tol=1e-4, cell_size=a.cell_size)

    facts, page_facts = {}, []
    for item in ref.get("items", []):
        if item.get("resolution_status") == "VERIFIED" and item.get("pin_identity") and item.get("net_identity"):
            key = (item.get("source_page"), item.get("symbol_identity"),
                   item.get("pin_identity"), item.get("net_identity"))
            facts[key] = item
            if item.get("source_page") == a.page:
                page_facts.append({
                    "resolution_id": item.get("resolution_id"),
                    "symbol": item.get("symbol_identity"),
                    "pin": item.get("pin_identity"),
                    "net": item.get("net_identity")
                })
    rels = Counter(x["relation"] for x in geom["candidates"])
    naive = len(segments) * (len(segments) - 1) // 2
    result = {
        "schema": "qi9-g3-g4-page-run/v1",
        "source_pdf": a.pdf.name, "source_sha256": source_hash,
        "pdf_page_index_1based": a.page,
        "render": {"scale": a.scale, "width_px": pix.width, "height_px": pix.height},
        "g3": {
            "ocr_word_occurrences": len(tokens),
            "refdes_candidate_occurrences": len(refs),
            "refdes_unique_tokens": sorted(set(refs)),
            "net_label_candidate_occurrences": len(nets),
            "net_label_unique_tokens": sorted(set(nets)),
            "reviewed_pin_net_facts_unique_across_source": len(facts),
            "reviewed_pin_net_facts_on_page": page_facts,
            "status": "PARTIAL_CANDIDATES_ONLY"
        },
        "g4": {
            "raw_line_primitive_count": line_count,
            "spatial_broadphase_pair_count": geom["broadphase_pair_count"],
            "naive_all_pairs_count": naive,
            "broadphase_pruning_percent": round((1 - geom["broadphase_pair_count"] / naive) * 100, 4) if naive else 0,
            "geometric_relation_candidate_count": geom["candidate_count"],
            "geometric_relation_counts": dict(rels),
            "wire_role_classified_segments": 0,
            "verified_junction_dots": 0,
            "verified_joins": geom["verified_join_count"],
            "net_membership_claims": 0,
            "status": "GEOMETRY_ONLY_BLOCKED"
        },
        "release": {"status": "BLOCKED_ELECTRICAL_TOPOLOGY",
                    "kicad_netlist_generated": False, "mutation_performed": False},
        "limitations": [
            "OCR tokens are candidates, not verified symbols or labels.",
            "Every vector line is role UNKNOWN and includes wires, symbol outlines, glyphs, and frames.",
            "Spatial index prunes bounding-box pairs only; geometry is not electrical connectivity.",
            "No junction-dot or pin-anchor classifier is promoted by this run."
        ]
    }
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["release"]["status"], "source_sha256": source_hash,
                      "page": a.page, "ocr_words": len(tokens), "line_primitives": line_count,
                      "geometric_candidates": geom["candidate_count"],
                      "verified_joins": geom["verified_join_count"], "net_membership_claims": 0,
                      "output": str(a.output)}, indent=2))

if __name__ == "__main__":
    main()
