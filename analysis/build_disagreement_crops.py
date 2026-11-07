#!/usr/bin/env python3
"""Build visual evidence crops for the 27 DeepSeek/Manus disagreements.

Coordinates in the register are PDF points. The upstream SVGs may use either
PDF top-left or bottom-left Y convention, so the pack deliberately emits both
Y interpretations. This script does not adjudicate pin identity or connectivity.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import cairosvg
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "analysis/qi9-deepseek-manus-disagreement-register.csv"
SVG_ROOT = ROOT / "QI9-2604-A01_G3-G4_PAGE6_REFINEMENT/artifacts/g0_intake"
OUT = ROOT / "analysis/disagreement-crops"
SCALE = 2.0
HALF_W_PT = 34.0
HALF_H_PT = 28.0


def svg_dimensions(svg: bytes) -> tuple[float, float]:
    head = svg[:4096].decode("utf-8", "ignore")
    def dim(name: str) -> float:
        m = re.search(rf'\b{name}="([0-9.]+)', head)
        if not m:
            raise ValueError(f"missing SVG {name}")
        return float(m.group(1))
    return dim("width"), dim("height")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with REGISTER.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != 27:
        raise SystemExit(f"expected 27 disagreements, got {len(rows)}")

    page_cache: dict[int, tuple[Image.Image, float, float]] = {}
    for row in rows:
        page = int(row["page"])
        if page not in page_cache:
            svg_path = SVG_ROOT / f"page-{page:03d}.svg"
            svg = svg_path.read_bytes()
            width_pt, height_pt = svg_dimensions(svg)
            png = cairosvg.svg2png(bytestring=svg, scale=SCALE)
            image = Image.open(io.BytesIO(png)).convert("RGB")
            page_cache[page] = (image, width_pt, height_pt)

    manifest_rows = []
    for row in rows:
        page = int(row["page"])
        x = float(row["terminal_x_pdf_pt"])
        y = float(row["terminal_y_pdf_pt"])
        image, page_w, page_h = page_cache[page]
        for convention, cy in (("svg-y-direct", y), ("pdf-y-flipped", page_h - y)):
            cx_px, cy_px = x * SCALE, cy * SCALE
            box = (
                max(0, int((x - HALF_W_PT) * SCALE)),
                max(0, int((cy - HALF_H_PT) * SCALE)),
                min(image.width, int((x + HALF_W_PT) * SCALE)),
                min(image.height, int((cy + HALF_H_PT) * SCALE)),
            )
            crop = image.crop(box)
            draw = ImageDraw.Draw(crop)
            mx, my = cx_px - box[0], cy_px - box[1]
            draw.line((mx - 12, my, mx + 12, my), fill=(255, 0, 0), width=2)
            draw.line((mx, my - 12, mx, my + 12), fill=(255, 0, 0), width=2)
            name = f'{row["case_id"]}-p{page:02d}-{convention}.png'
            crop.save(OUT / name)
            manifest_rows.append({
                "case_id": row["case_id"], "page": page, "x_pdf_pt": x,
                "y_pdf_pt": y, "y_convention": convention,
                "crop_file": name, "crop_box_pdf_pt": box,
                "deepseek_ref": row["deepseek_ref"],
                "deepseek_pin": row["deepseek_pin"],
                "deepseek_status": row["deepseek_pin_identity_status"],
                "manus_ref": row["manus_ref"], "manus_pin": row["manus_pin"],
                "manus_status": row["manus_pin_identity_status"],
            })

    with (OUT / "crop_manifest.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(manifest_rows[0]))
        writer.writeheader()
        writer.writerows(manifest_rows)

    # Contact sheets are split by page and Y convention to keep text legible.
    for page in sorted(page_cache):
        for convention in ("svg-y-direct", "pdf-y-flipped"):
            subset = [r for r in manifest_rows if r["page"] == page and r["y_convention"] == convention]
            thumbs = []
            for r in subset:
                im = Image.open(OUT / r["crop_file"]).convert("RGB")
                im.thumbnail((520, 360))
                tile = Image.new("RGB", (540, 405), "white")
                tile.paste(im, ((540-im.width)//2, 32))
                d = ImageDraw.Draw(tile)
                d.text((10, 8), f'{r["case_id"]} {r["deepseek_ref"]}/{r["deepseek_pin"] or "?"} vs {r["manus_ref"]}/{r["manus_pin"] or "?"}', fill="black")
                thumbs.append(tile)
            cols = 2
            rows_n = (len(thumbs) + cols - 1) // cols
            sheet = Image.new("RGB", (cols*540, rows_n*405), "#dddddd")
            for i, im in enumerate(thumbs):
                sheet.paste(im, ((i % cols)*540, (i // cols)*405))
            sheet.save(OUT / f"contact-p{page:02d}-{convention}.jpg", quality=90)

    (OUT / "README.md").write_text(
        "# Disagreement crop pack\n\n"
        "Generated from the committed G0 page SVGs and the 27-row disagreement register. "
        "Each case has two crops because the register does not yet establish whether its Y "
        "coordinate is top-origin or bottom-origin. Red cross marks the tested coordinate. "
        "These images are visual evidence only; they do not establish pin identity, symbol "
        "library mapping, wire primitive provenance, or electrical connectivity.\n",
        encoding="utf-8",
    )
    print(f"generated {len(rows)} cases / {len(manifest_rows)} crops / {len(page_cache)} pages")


if __name__ == "__main__":
    main()
