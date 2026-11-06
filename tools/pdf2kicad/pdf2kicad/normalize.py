"""Front end: decide what kind of PDF page this is and give the pipeline one common input.

kinds
  strokefont  vector geometry, text drawn as tiny stroke polylines (KiCad plot)   -> glyph decoder
  textlayer   vector geometry + real text (Altium/OrCAD/Eagle/Xpedition/LibreOffice exports) -> text layer
  raster      scanned / image only                                                -> not extractable here
  empty       nothing usable
"""
from dataclasses import dataclass
import re
import pymupdf

REFLIKE = re.compile(r"^[A-Z]{1,3}\d+[A-Z]?$")


@dataclass
class PageKind:
    kind: str
    segs: int
    short_segs: int
    words: int
    reflike: int
    images: int
    rotation: int
    note: str = ""


def classify(pdf, page_index):
    doc = pymupdf.open(pdf)
    if not 0 <= page_index < len(doc):
        raise ValueError("page %d out of range (document has %d pages)" % (page_index + 1, len(doc)))
    pg = doc[page_index]
    words = [w[4] for w in pg.get_text("words")]
    reflike = sum(1 for t in words if REFLIKE.match(t))
    segs = short = 0
    for d in pg.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                segs += 1
                if abs(it[1].x - it[2].x) + abs(it[1].y - it[2].y) <= 5.0:
                    short += 1
            elif it[0] in ("re", "qu", "c"):
                segs += 1
    images = len(pg.get_images(full=True))
    k = PageKind("empty", segs, short, len(words), reflike, images, pg.rotation)
    if segs < 30 and images:
        k.kind, k.note = "raster", "image-only page: no vector geometry. Needs OCR/vision (see README 'Other PDFs')"
    elif segs < 30:
        k.note = "no usable vector geometry"
    elif reflike >= 8:                       # a real text layer carries the part references
        k.kind = "textlayer"
    elif short >= 0.2 * segs:                # title-block text only; the drawing text is stroke polylines
        k.kind = "strokefont"
    elif len(words) >= 40:
        k.kind = "textlayer"
    else:
        k.kind, k.note = "textlayer", "few words found; result will be partial"
    if k.kind == "textlayer" and images and segs < 200:
        k.note = "mixed page (images + little vector data); result may be partial"
    return k


class TextWord:
    """Word taken from the PDF text layer; same attributes the pipeline uses on decoded words."""

    def __init__(self, x0, y0, x1, y1, text):
        self.x0, self.y0, self.x1, self.y1, self.text = x0, y0, x1, y1, text
        self.glyphs = []
        self.rot = 90 if (y1 - y0) > 1.6 * (x1 - x0) and len(text) > 1 else 0
        self.score = 0.0
        self.em = max(3.0, min(x1 - x0, y1 - y0) if self.rot == 0 else x1 - x0)


def text_layer_words(page):
    return [TextWord(x0, y0, x1, y1, t) for (x0, y0, x1, y1, t) in page.words if t.strip()]


def estimate_scale(page, lo=0.1, hi=8.0):
    """Drawing scale so that the 100-mil grid (7.2pt) is what the pipeline expects.
    Score each candidate scale by how many H/V segment lengths land on 3.6/7.2/14.4 pt (7.2 weighted most).
    Returns (scale, peak_count); peak_count < 20 means 'no clear grid', keep 1.0."""
    import numpy as np
    L = np.array([s.length for s in page.segs if s.kind == "line" and (s.is_h or s.is_v) and s.length > 0.3])
    if len(L) < 20:
        return 1.0, 0
    best, bs, base = 1.0, -1.0, 0
    s = lo
    while s <= hi:
        x = L * s
        sc = 3 * np.sum(np.abs(x - 7.2) < 0.1) + np.sum(np.abs(x - 14.4) < 0.15) + 0.5 * np.sum(np.abs(x - 3.6) < 0.06)
        if sc > bs:
            best, bs = s, sc
        s *= 1.01
    n = int(np.sum(np.abs(L * best - 7.2) < 0.1))
    return round(best, 3), n
