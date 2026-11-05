"""KiCad "newstroke" font: exact stroke templates for every ASCII glyph.

KiCad plots schematic text as stroke polylines taken from this font, so a glyph found in the PDF can
be recognised by shape instead of OCR.  The font data (KiCad project, SIL OFL) is not bundled; run
`python tools/fetch_font.py` once to download it from the KiCad GitHub mirror.
"""
import json
import math
import os
import re
import numpy as np

SCALE = 1.0 / 21.0
Y_OFFSET = -8
BASE_Y = 1.0 / 21.0            # y of the baseline in font coordinates (y grows downwards)
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "newstroke_ascii.json")


def decode_glyph(code):
    """newstroke string -> list of strokes, each a list of (x, y_up) in em units (x from glyph origin)."""
    left = (ord(code[0]) - ord("R")) * SCALE
    strokes, cur = [], []
    i = 2
    while i + 1 < len(code) + 1 and i < len(code):
        a, b = code[i], code[i + 1]
        if a == " " and b == "R":
            if cur:
                strokes.append(cur)
            cur = []
        else:
            x = (ord(a) - ord("R")) * SCALE - left
            y = (ord(b) - ord("R") + Y_OFFSET) * SCALE
            cur.append((x, BASE_Y - y))          # y up from baseline
        i += 2
    if cur:
        strokes.append(cur)
    return strokes


def load_codes(path=DATA):
    if not os.path.exists(path):
        raise FileNotFoundError(
            "KiCad font data not found at %s. Run: python tools/fetch_font.py" % os.path.abspath(path))
    return json.load(open(path))


class Template:
    __slots__ = ("char", "pts", "x1", "y0", "y1", "single_dot")

    def __init__(self, char, strokes):
        self.char = char
        pts = []
        for st in strokes:
            if len(st) == 1:
                pts.append(st[0])
            for a, b in zip(st, st[1:]):
                L = math.hypot(b[0] - a[0], b[1] - a[1])
                n = max(1, int(L / 0.06))
                for k in range(n + 1):
                    t = k / n
                    pts.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        arr = np.array(pts)
        x0 = arr[:, 0].min()
        arr[:, 0] -= x0                       # x measured from the left ink edge, like the observed glyphs
        self.pts = arr
        self.x1 = arr[:, 0].max()
        self.y0, self.y1 = arr[:, 1].min(), arr[:, 1].max()


def _need_font():
    if not os.path.exists(DATA):
        raise SystemExit("font data missing: %s\nrun: python tools/fetch_font.py  (needs git + github access)" % os.path.abspath(DATA))


def load_templates(path=DATA):
    if path == DATA:
        _need_font()
    codes = load_codes(path)
    out = []
    for ch, code in codes.items():
        if ch == " " or not code:
            continue
        strokes = decode_glyph(code)
        if strokes:
            out.append(Template(ch, strokes))
    return out
