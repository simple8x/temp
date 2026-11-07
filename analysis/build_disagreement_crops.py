#!/usr/bin/env python3
"""Build visual evidence crops from the exact source PDF for 27 disagreements.

The register coordinates may use either top-left or bottom-left Y origin.
Both interpretations are emitted; this script deliberately does not adjudicate
pin identity, symbol-library mapping, or electrical connectivity.
"""
from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "analysis/qi9-deepseek-manus-disagreement-register.csv"
SOURCE_PDF = ROOT / "QI9-2604-A01_G3-G4_PAGE6_REFINEMENT/-QI9-2604-A01- Module IO.pdf"
OUT = ROOT / "analysis/disagreement-crops"
EXPECTED_SHA256 = "a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc"
SCALE = 2.0
HALF_W_PT = 34.0
HALF_H_PT = 28.0


def main() -> None:
    if not SOURCE_PDF.is_file():
        raise SystemExit(f"source PDF missing: {SOURCE_PDF}")
    digest = hashlib.sha256(SOURCE_PDF.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA256:
        raise SystemExit(f"source PDF SHA-256 mismatch: {digest}")

    with REGISTER.open(newline="", encoding="utf-8") as f:
        cases = list(csv.DictReader(f))
    if len(cases) != 27:
        raise SystemExit(f"expected 27 disagreements, got {len(cases)}")

    OUT.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(SOURCE_PDF)
    manifest = []
    for case in cases:
        page_no = int(case["page"])
        page = doc[page_no - 1]
        page_h = float(page.rect.height)
        x = float(case["terminal_x_pdf_pt"])
        y = float(case["terminal_y_pdf_pt"])

        for convention, cy in (("pdf-y-direct", y), ("pdf-y-flipped", page_h - y)):
            rect = fitz.Rect(
                max(0, x - HALF_W_PT), max(0, cy - HALF_H_PT),
                min(page.rect.width, x + HALF_W_PT), min(page_h, cy + HALF_H_PT)
            )
            pix = page.get_pixmap(matrix=fitz.Matrix(SCALE, SCALE), clip=rect, alpha=False)
            crop = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            draw = ImageDraw.Draw(crop)
            mx, my = (x - rect.x0) * SCALE, (cy - rect.y0) * SCALE
            draw.line((mx - 12, my, mx + 12, my), fill=(255, 0, 0), width=2)
            draw.line((mx, my - 12, mx, my + 12), fill=(255, 0, 0), width=2)
            filename = f'{case["case_id"]}-p{page_no:02d}-{convention}.png'
            crop.save(OUT / filename)
            manifest.append({
                "case_id": case["case_id"], "page": page_no,
                "x_pdf_pt": x, "y_pdf_pt": y,
                "y_convention": convention, "crop_file": filename,
                "deepseek_ref": case["deepseek_ref"],
                "deepseek_pin": case["deepseek_pin"],
                "deepseek_status": case["deepseek_pin_identity_status"],
                "manus_ref": case["manus_ref"],
                "manus_pin": case["manus_pin"],
                "manus_status": case["manus_pin_identity_status"],
            })

    with (OUT / "crop_manifest.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(manifest[0]))
        writer.writeheader()
        writer.writerows(manifest)

    # Legible per-page contact sheets, one for each possible Y convention.
    for page_no in sorted({int(c["page"]) for c in cases}):
        for convention in ("pdf-y-direct", "pdf-y-flipped"):
            subset = [r for r in manifest if r["page"] == page_no and r["y_convention"] == convention]
            tiles = []
            for row in subset:
                crop = Image.open(OUT / row["crop_file"]).convert("RGB")
                crop.thumbnail((500, 300))
                tile = Image.new("RGB", (520, 345), "white")
                tile.paste(crop, ((520 - crop.width) // 2, 35))
                label = (f'{row["case_id"]}: '
                         f'{row["deepseek_ref"] or "?"}.{row["deepseek_pin"] or "?"} '
                         f'vs {row["manus_ref"] or "?"}.{row["manus_pin"] or "?"}')
                ImageDraw.Draw(tile).text((8, 8), label, fill="black")
                tiles.append(tile)
            cols = 2
            rows_n = (len(tiles) + cols - 1) // cols
            sheet = Image.new("RGB", (cols * 520, rows_n * 345), "#dddddd")
            for i, tile in enumerate(tiles):
                sheet.paste(tile, ((i % cols) * 520, (i // cols) * 345))
            sheet.save(OUT / f"contact-p{page_no:02d}-{convention}.jpg", quality=92)

    (OUT / "README.md").write_text(
        "# Disagreement crop pack\n\n"
        f"Source PDF SHA-256: {digest}. Generated {len(cases)} cases and "
        f"{len(manifest)} crops from the original PDF, not the intermediate SVG. "
        "Each case has both Y-origin interpretations because the register alone "
        "does not establish the coordinate convention. Red cross marks the tested "
        "coordinate. Images are visual evidence only; they do not establish pin "
        "identity, symbol-library mapping, source-wire primitive provenance, or "
        "electrical connectivity.\n",
        encoding="utf-8",
    )
    print(f"source_sha256={digest}; cases={len(cases)}; crops={len(manifest)}")


if __name__ == "__main__":
    main()
