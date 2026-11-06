"""Step 2: find text.  KiCad plots text as stroke-font polylines, so there is no text layer.

* short segments that touch or cross form glyphs;
* glyphs on one baseline form words (horizontal text, or text rotated 90 deg);
* every glyph is turned into a scale-free point cloud that can be matched against a font table.
"""
from collections import defaultdict
from dataclasses import dataclass, field
import math
import numpy as np

MAX_STROKE = 4.9        # longest single stroke of a glyph, pt
MAX_GLYPH = 5.8         # largest glyph bounding box, pt
MIN_GLYPH = 0.2
LINK_GAP = 2.0          # max gap between neighbouring glyphs of one word, pt


# ----------------------------------------------------------------- geometry helpers
def seg_dist(a, b):
    """Minimum distance between two segments given as (x0,y0,x1,y1)."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b

    def orient(px, py, qx, qy, rx, ry):
        return (qx - px) * (ry - py) - (qy - py) * (rx - px)

    o1 = orient(ax0, ay0, ax1, ay1, bx0, by0)
    o2 = orient(ax0, ay0, ax1, ay1, bx1, by1)
    o3 = orient(bx0, by0, bx1, by1, ax0, ay0)
    o4 = orient(bx0, by0, bx1, by1, ax1, ay1)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return 0.0

    def pt(px, py, x0, y0, x1, y1):
        dx, dy = x1 - x0, y1 - y0
        L = dx * dx + dy * dy
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / L))
        return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))

    return min(pt(ax0, ay0, bx0, by0, bx1, by1), pt(ax1, ay1, bx0, by0, bx1, by1),
               pt(bx0, by0, ax0, ay0, ax1, ay1), pt(bx1, by1, ax0, ay0, ax1, ay1))


class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


@dataclass
class Glyph:
    segs: list                      # [(x0,y0,x1,y1)] in page coordinates
    x0: float = 0
    y0: float = 0
    x1: float = 0
    y1: float = 0
    char: str = ""                  # filled in by the decoder
    score: float = 9.9

    def __post_init__(self):
        xs = [v for s in self.segs for v in (s[0], s[2])]
        ys = [v for s in self.segs for v in (s[1], s[3])]
        self.x0, self.x1, self.y0, self.y1 = min(xs), max(xs), min(ys), max(ys)

    @property
    def w(self):
        return self.x1 - self.x0

    @property
    def h(self):
        return self.y1 - self.y0


@dataclass
class Word:
    glyphs: list
    rot: int = 0                    # 0 = horizontal, 90 = reads bottom-to-top
    em: float = 0.0
    text: str = ""
    score: float = 9.9
    x0: float = 0
    y0: float = 0
    x1: float = 0
    y1: float = 0

    def update_box(self):
        self.x0 = min(g.x0 for g in self.glyphs)
        self.y0 = min(g.y0 for g in self.glyphs)
        self.x1 = max(g.x1 for g in self.glyphs)
        self.y1 = max(g.y1 for g in self.glyphs)

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2


# --------------------------------------------------------------------- glyph finding
def find_glyphs(segs, tol=0.3):
    """Union short segments that touch/cross; keep components that are glyph sized."""
    cand, dup = [], set()
    for i, s in enumerate(segs):
        if s.length <= MAX_STROKE and s.kind != "rect":
            k = (round(s.x0, 2), round(s.y0, 2), round(s.x1, 2), round(s.y1, 2))
            if k in dup:                       # KiCad plots strokes twice; one copy is enough
                continue
            dup.add(k)
            cand.append((i, (s.x0, s.y0, s.x1, s.y1)))
    bb = [(min(g[0], g[2]) - tol, min(g[1], g[3]) - tol, max(g[0], g[2]) + tol, max(g[1], g[3]) + tol) for _, g in cand]
    cell = 3.0
    grid = defaultdict(list)
    for k, (_, g) in enumerate(cand):
        for cx in range(int((min(g[0], g[2]) - tol) // cell), int((max(g[0], g[2]) + tol) // cell) + 1):
            for cy in range(int((min(g[1], g[3]) - tol) // cell), int((max(g[1], g[3]) + tol) // cell) + 1):
                grid[(cx, cy)].append(k)
    uf = UF(len(cand))
    seen = set()
    for ks in grid.values():
        for a in range(len(ks)):
            for b in range(a + 1, len(ks)):
                i, j = ks[a], ks[b]
                if (i, j) in seen:
                    continue
                seen.add((i, j))
                bi, bj = bb[i], bb[j]
                if bi[2] < bj[0] or bj[2] < bi[0] or bi[3] < bj[1] or bj[3] < bi[1]:
                    continue                    # boxes do not even touch: skip the exact distance
                if uf.find(i) != uf.find(j) and seg_dist(cand[i][1], cand[j][1]) <= tol:
                    uf.union(i, j)
    comps = defaultdict(list)
    for k in range(len(cand)):
        comps[uf.find(k)].append(cand[k][1])
    out = []
    for c in comps.values():
        g = Glyph(c)
        if MIN_GLYPH <= max(g.w, g.h) <= MAX_GLYPH:
            out.append(g)
    return _merge_dots(out)


def _merge_dots(gl, small=1.3):
    """Dots of i, j, :, ; and ! are separate strokes: attach them to the glyph right below/above."""
    tiny = [g for g in gl if max(g.w, g.h) <= small]
    big = [g for g in gl if max(g.w, g.h) > small]
    merged = set()
    for t in tiny:
        cx = (t.x0 + t.x1) / 2
        best, bd = None, 1.7
        for b in big:
            if b.x0 - 0.25 <= cx <= b.x1 + 0.25:
                gap = b.y0 - t.y1 if t.y1 <= b.y0 + 0.2 else (t.y0 - b.y1 if t.y0 >= b.y1 - 0.2 else 9)
                if 0 <= gap < bd and b.h <= 3.2:          # stems of i/j are x-height tall, not caps
                    best, bd = b, gap
        if best is not None:
            best.segs.extend(t.segs)
            best.__post_init__()
            merged.add(id(t))
    return big + [t for t in tiny if id(t) not in merged]


# --------------------------------------------------------------------- word building
def _link(a, b, frame):
    """True if glyph boxes a,b (x0,y0,x1,y1 in the reading frame) are neighbours on one line."""
    gap = max(b[0] - a[2], a[0] - b[2])
    if gap > LINK_GAP:
        return False
    ah, bh = a[3] - a[1], b[3] - b[1]
    top, bot = max(a[1], b[1]), min(a[3], b[3])
    if bot - top > 0:
        return True
    # thin glyphs (- _ . ,) sit inside or just under the other glyph's line
    thin, other = (a, b) if ah < bh else (b, a)
    if thin[3] - thin[1] <= 1.3:
        return thin[1] >= other[1] - 0.6 and thin[3] <= other[3] + 1.6
    return False


def _to_frame(g, rot):
    if rot == 0:
        return (g.x0, g.y0, g.x1, g.y1)
    return (-g.y1, g.x0, -g.y0, g.x1)       # (x',y') = (-y, x)


def _cluster(glyphs, rot, exclude=()):
    ids = [i for i in range(len(glyphs)) if i not in exclude]
    boxes = {i: _to_frame(glyphs[i], rot) for i in ids}
    ids.sort(key=lambda i: boxes[i][0])
    uf = UF(len(glyphs))
    for pos, i in enumerate(ids):
        for j in ids[pos + 1:]:
            if boxes[j][0] - boxes[i][2] > LINK_GAP + 6:
                break
            if _link(boxes[i], boxes[j], rot):
                uf.union(i, j)
    groups = defaultdict(list)
    for i in ids:
        groups[uf.find(i)].append(i)
    return list(groups.values())


def build_words(glyphs):
    """Horizontal words first, then 90-degree words from the glyphs left over."""
    words, used = [], set()
    for grp in _cluster(glyphs, 0):
        if len(grp) >= 2:
            words.append(Word([glyphs[i] for i in sorted(grp, key=lambda k: glyphs[k].x0)], 0))
            used.update(grp)
    for grp in _cluster(glyphs, 90, exclude=used):
        if len(grp) >= 2:
            words.append(Word([glyphs[i] for i in sorted(grp, key=lambda k: -glyphs[k].y1)], 90))
            used.update(grp)
    for i, g in enumerate(glyphs):
        if i not in used:
            words.append(Word([g], 0))
    for w in words:
        w.update_box()
    return words


# ------------------------------------------------------------ scale free signatures
def frame_segs(g, rot):
    if rot == 0:
        return list(g.segs)
    return [(-s[1], s[0], -s[3], s[2]) for s in g.segs]


def sample_points(segs, step):
    pts = []
    for x0, y0, x1, y1 in segs:
        L = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(L / step))
        for k in range(n + 1):
            t = k / n
            pts.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return np.array(pts)


def word_metrics(word, page_em):
    """em (cap height) and baseline of a word in its reading frame."""
    boxes = [_to_frame(g, word.rot) for g in word.glyphs]
    em = page_em                      # one text size per sheet in KiCad plots
    bottoms = sorted(b[3] for b in boxes if (b[3] - b[1]) >= 0.6 * em)
    base = bottoms[len(bottoms) // 2] if bottoms else boxes[0][3]
    return em, base


def glyph_signature(g, rot, em, base):
    """Point cloud in em units: x from the glyph's left edge, y up from the baseline."""
    segs = frame_segs(g, rot)
    xs = [v for s in segs for v in (s[0], s[2])]
    x0 = min(xs)
    pts = sample_points(segs, 0.06 * em)
    return np.column_stack(((pts[:, 0] - x0) / em, (base - pts[:, 1]) / em))


def chamfer(a, b, return_max=False):
    d = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1))
    da, db = d.min(1), d.min(0)
    mean = 0.5 * (da.mean() + db.mean())
    if return_max:
        return mean, max(da.max(), db.max())
    return mean


def estimate_text_sizes(words, min_count=3, tol=0.2):
    """All text sizes (cap heights, pt) used on the page, most common first."""
    hs = []
    for w in words:
        if len(w.glyphs) >= 2:
            b = [_to_frame(g, w.rot) for g in w.glyphs]
            hs.append(max(x[3] - x[1] for x in b))
    if not hs:
        return [3.6]
    hs.sort()
    clusters, cur = [], [hs[0]]
    for h in hs[1:]:
        if h - cur[-1] <= tol:
            cur.append(h)
        else:
            clusters.append(cur)
            cur = [h]
    clusters.append(cur)
    clusters = [c for c in clusters if len(c) >= min_count and np.median(c) >= 1.4]
    clusters.sort(key=len, reverse=True)
    out = [round(float(np.median(c)), 2) for c in clusters]
    return out or [3.6]


def estimate_page_em(words):
    hs = []
    for w in words:
        if len(w.glyphs) >= 3:
            b = [_to_frame(g, w.rot) for g in w.glyphs]
            hs.append(round(max(x[3] - x[1] for x in b), 1))
    if not hs:
        return 3.6
    vals, cnt = np.unique(hs, return_counts=True)
    return float(vals[cnt.argmax()])
