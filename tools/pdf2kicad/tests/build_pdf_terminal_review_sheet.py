"""Build a human-review PDF contact sheet for unresolved PDF terminals.

Coordinates in reference_connectivity_review.json are unrotated, top-left-origin
PDF points, matching pdf2kicad.primitives.load_page().
No pin or net is inferred or promoted by this tool.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path

import pymupdf


def _label(entry: dict) -> str:
    return (
        f"p{entry['page']} | {entry.get('classification', 'UNKNOWN')} | "
        f"ref={entry.get('ref_candidate') or '?'} | "
        f"pin={entry.get('pin_candidate') or '?'} | "
        f"id={entry.get('graph_node_candidate') or '?'}"
    )


def build_review_sheet(
    pdf_path: Path,
    ledger_path: Path,
    output_dir: Path,
    context_radius: float = 90.0,
    scale: float = 2.0,
    cards_per_page: int = 6,
) -> dict:
    if context_radius <= 0 or scale <= 0 or cards_per_page < 1:
        raise ValueError("context_radius, scale and cards_per_page must be positive")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    digest = ledger.get("source_pdf_sha256")
    import hashlib
    actual = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    if digest and digest != actual:
        raise ValueError(f"source PDF SHA256 mismatch: ledger={digest}, actual={actual}")

    entries = ledger.get("entries")
    if not isinstance(entries, list):
        raise ValueError("ledger.entries must be a list")

    doc = pymupdf.open(pdf_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    sheet = pymupdf.open()
    review_rows = []
    page_width, page_height = 595.0, 842.0
    margin, label_h, gap = 24.0, 30.0, 12.0
    cols = 2
    rows = (cards_per_page + cols - 1) // cols
    card_w = (page_width - 2 * margin - gap) / cols
    card_h = (page_height - 2 * margin - gap * (rows - 1)) / rows

    for start in range(0, len(entries), cards_per_page):
        out_page = sheet.new_page(width=page_width, height=page_height)
        batch = entries[start:start + cards_per_page]
        for offset, entry in enumerate(batch):
            page_no = int(entry["page"])
            if page_no < 1 or page_no > len(doc):
                raise ValueError(f"entry page {page_no} outside PDF page range")
            xy = entry.get("terminal_xy_pdf_pt")
            if not isinstance(xy, list) or len(xy) != 2:
                raise ValueError(f"entry missing terminal_xy_pdf_pt: {entry}")
            x, y = float(xy[0]), float(xy[1])
            source_page = doc[page_no - 1]
            bounds = source_page.rect
            clip = pymupdf.Rect(
                max(bounds.x0, x - context_radius),
                max(bounds.y0, y - context_radius),
                min(bounds.x1, x + context_radius),
                min(bounds.y1, y + context_radius),
            )
            if clip.is_empty or clip.is_infinite:
                raise ValueError(f"invalid crop for page {page_no} at {xy}")
            pix = source_page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False)
            png = pix.tobytes("png")

            col, row = offset % cols, offset // cols
            x0 = margin + col * (card_w + gap)
            y0 = margin + row * (card_h + gap)
            card = pymupdf.Rect(x0, y0, x0 + card_w, y0 + card_h)
            out_page.draw_rect(card, color=(0.25, 0.25, 0.25), width=0.6)
            out_page.insert_text((x0 + 7, y0 + 15), _label(entry)[:110], fontsize=7)
            out_page.insert_text(
                (x0 + 7, y0 + 26),
                f"PDF pt=({x:.2f}, {y:.2f}) | comp={entry.get('graph_component_candidate') or '?'}",
                fontsize=6,
            )
            image_rect = pymupdf.Rect(x0 + 6, y0 + label_h, x0 + card_w - 6, y0 + card_h - 6)
            out_page.insert_image(image_rect, stream=png, keep_proportion=True)
            review_rows.append({
                "review_entry_index": start + offset,
                "page": page_no,
                "classification_candidate": entry.get("classification", ""),
                "ref_candidate": entry.get("ref_candidate") or "",
                "pin_candidate": entry.get("pin_candidate") or "",
                "candidate_net_diagnostic_only": entry.get("candidate_net_name_diagnostic_only") or "",
                "terminal_x_pdf_pt": x,
                "terminal_y_pdf_pt": y,
                "graph_component_candidate": entry.get("graph_component_candidate") or "",
                "graph_node_candidate": entry.get("graph_node_candidate") or "",
                "source_wire_primitive_ids": ";".join(entry.get("source_wire_primitive_ids") or []),
                "human_decision": "",
                "reference_pin": "",
                "reference_net": "",
                "reviewer": "",
                "evidence_notes": "",
            })

    pdf_out = output_dir / "terminal_reference_review_sheet.pdf"
    sheet.save(pdf_out, garbage=4, deflate=True)
    sheet.close()
    doc.close()

    csv_out = output_dir / "terminal_reference_review.csv"
    fields = list(review_rows[0]) if review_rows else [
        "review_entry_index", "page", "classification_candidate", "ref_candidate",
        "pin_candidate", "candidate_net_diagnostic_only", "terminal_x_pdf_pt",
        "terminal_y_pdf_pt", "graph_component_candidate", "graph_node_candidate",
        "source_wire_primitive_ids", "human_decision", "reference_pin",
        "reference_net", "reviewer", "evidence_notes",
    ]
    with csv_out.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(review_rows)

    manifest = {
        "schema": "pdf2kicad-terminal-review-sheet/v1",
        "source_pdf_sha256": actual,
        "ledger_schema": ledger.get("schema"),
        "entries_rendered": len(review_rows),
        "pages_in_sheet": (len(review_rows) + cards_per_page - 1) // cards_per_page,
        "coordinate_system": "unrotated PDF page coordinates, top-left origin, points",
        "crop_context_radius_pt": context_radius,
        "render_scale": scale,
        "pdf": pdf_out.name,
        "csv": csv_out.name,
        "policy": "visual evidence only; no automatic pin/net assignment or VERIFIED promotion",
    }
    (output_dir / "terminal_reference_review_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--radius", type=float, default=90.0)
    parser.add_argument("--scale", type=float, default=2.0)
    parser.add_argument("--cards-per-page", type=int, default=6)
    args = parser.parse_args()
    print(json.dumps(build_review_sheet(
        args.pdf, args.ledger, args.out, args.radius, args.scale, args.cards_per_page
    ), indent=2))


if __name__ == "__main__":
    main()
