"""Step 1: pull drawing primitives out of one PDF page.

Everything is reduced to straight segments (curves are flattened) plus the set of small
filled dots (junctions).  Units are PDF points, y grows downwards.
"""
from dataclasses import dataclass, field
import math
import pymupdf


@dataclass
class Seg:
    x0: float
    y0: float
    x1: float
    y1: float
    kind: str = "line"          # line | curve | rect

    @property
    def length(self):
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)

    @property
    def is_h(self):
        return abs(self.y1 - self.y0) < 0.06

    @property
    def is_v(self):
        return abs(self.x1 - self.x0) < 0.06


@dataclass
class Page:
    width: float
    height: float
    segs: list = field(default_factory=list)
    dots: list = field(default_factory=list)       # (x, y) centres of small filled blobs
    words: list = field(default_factory=list)      # real text layer: (x0, y0, x1, y1, text)


def _bezier(p, n=6):
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        x = u ** 3 * p[0].x + 3 * u * u * t * p[1].x + 3 * u * t * t * p[2].x + t ** 3 * p[3].x
        y = u ** 3 * p[0].y + 3 * u * u * t * p[1].y + 3 * u * t * t * p[2].y + t ** 3 * p[3].y
        pts.append((x, y))
    return pts


def load_page(pdf_path, page_index, scale=1.0, rotate=0):
    doc = pymupdf.open(pdf_path)
    if not 0 <= page_index < len(doc):
        raise ValueError("page %d out of range (document has %d pages)" % (page_index + 1, len(doc)))
    pg = doc[page_index]
    # Work in the page's stored (unrotated) coordinates: the /Rotate flag is only a viewer setting.
    # Content that is stored sideways is fixed explicitly with rotate=90/180/270 (clockwise).
    mb = pg.mediabox
    W, H = mb.width, mb.height
    def P(x, y):
        x, y = x - mb.x0, y - mb.y0
        if rotate == 90:
            x, y = H - y, x
        elif rotate == 180:
            x, y = W - x, H - y
        elif rotate == 270:
            x, y = y, W - x
        return x * scale, y * scale
    page = Page((H if rotate in (90, 270) else W) * scale, (W if rotate in (90, 270) else H) * scale)
    for w in pg.get_text("words"):
        a, b = P(w[0], w[1]), P(w[2], w[3])
        page.words.append((min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]), w[4]))
    for d in pg.get_drawings():
        filled = d.get("fill") is not None and tuple(d["fill"]) != (1.0, 1.0, 1.0)
        r = d["rect"]
        if filled and max(r.width, r.height) <= 3.0 and min(r.width, r.height) >= 0.6:
            # junction dots are tiny filled blobs
            page.dots.append(P((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
            continue
        for it in d["items"]:
            k = it[0]
            if k == "l":
                a, b = P(it[1].x, it[1].y), P(it[2].x, it[2].y); page.segs.append(Seg(a[0], a[1], b[0], b[1], "line"))
            elif k == "c":
                pts = [P(*p_) for p_ in _bezier([it[1], it[2], it[3], it[4]])]
                for a, b in zip(pts, pts[1:]):
                    page.segs.append(Seg(a[0], a[1], b[0], b[1], "curve"))
            elif k == "re":
                q = it[1]
                c = [P(q.x0, q.y0), P(q.x1, q.y0), P(q.x1, q.y1), P(q.x0, q.y1)]
                for a, b in zip(c, c[1:] + c[:1]):
                    page.segs.append(Seg(a[0], a[1], b[0], b[1], "rect"))
            elif k == "qu":
                q = it[1]
                c = [P(q.ul.x, q.ul.y), P(q.ur.x, q.ur.y), P(q.lr.x, q.lr.y), P(q.ll.x, q.ll.y)]
                for a, b in zip(c, c[1:] + c[:1]):
                    page.segs.append(Seg(a[0], a[1], b[0], b[1], "rect"))
    page.segs = [s for s in page.segs if s.length > 1e-6]
    return page
