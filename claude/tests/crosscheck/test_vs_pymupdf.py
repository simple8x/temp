"""Cross-check against an independent extractor (PyMuPDF / MuPDF).

Independence note (FACT): MuPDF shares no code with pikepdf/ecci. Agreement here shows the
two extractors agree on this data; it does NOT prove either is correct on all PDFs.
If PyMuPDF is missing the tests are SKIPPED, which the CI treats as a failure (UNKNOWN != PASS).
"""

from __future__ import annotations

import io
import math
import random
import warnings

import pytest

warnings.filterwarnings("ignore", category=FutureWarning)
pymupdf = pytest.importorskip("pymupdf")

from ecci.ingest_vector import ingest_pdf
from pdfgen import Form, PageSpec, build_pdf

pytestmark = pytest.mark.crosscheck
TOL_POS = 0.05 + 2e-3          # flatten tol + coordinate rounding of the oracle comparison


def seg_dist(p, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def bez(c, t):
    u = 1 - t
    return (u**3 * c[0][0] + 3*u*u*t * c[1][0] + 3*u*t*t * c[2][0] + t**3 * c[3][0],
            u**3 * c[0][1] + 3*u*u*t * c[1][1] + 3*u*t*t * c[2][1] + t**3 * c[3][1])


def mupdf_geometry(page):
    """Return (segments, key_points, widths) in displayed (rotated) page space."""
    rm = page.rotation_matrix
    def T(pt):
        q = pymupdf.Point(pt) * rm
        return (q.x, q.y)
    segs, keys, widths = [], [], []
    for d in page.get_drawings():
        stroked = d.get("type") in ("s", "fs")
        if stroked:
            widths.append(round(d.get("width") or 0.0, 4))
        for it in d["items"]:
            k = it[0]
            if k == "l":
                a, b = T(it[1]), T(it[2])
                segs.append((a, b)); keys += [a, b]
            elif k == "c":
                c = [T(it[i]) for i in (1, 2, 3, 4)]
                prev = c[0]
                for i in range(1, 401):
                    cur = bez(c, i / 400)
                    segs.append((prev, cur)); prev = cur
                keys += [c[0], c[3]]
            elif k == "re":
                r = it[1]
                pts = [T((r.x0, r.y0)), T((r.x1, r.y0)), T((r.x1, r.y1)), T((r.x0, r.y1))]
                segs += [(pts[i], pts[(i + 1) % 4]) for i in range(4)]; keys += pts
            elif k == "qu":
                q = it[1]
                pts = [T(q.ul), T(q.ur), T(q.lr), T(q.ll)]
                segs += [(pts[i], pts[(i + 1) % 4]) for i in range(4)]; keys += pts
    return segs, keys, widths


def random_content(rnd: random.Random):
    ops, n_paths = [], 0
    def num(lo=-20, hi=220):
        return round(rnd.uniform(lo, hi), 2)
    for _ in range(rnd.randint(3, 9)):
        if rnd.random() < 0.5:
            ops.append("q")
            a = rnd.choice([1, 1, 2, 0.5, -1]); d = rnd.choice([1, 1, 2, 0.5, -1])
            if rnd.random() < 0.3:
                ops.append(f"0 {a} {-d} 0 {num(0, 50)} {num(0, 50)} cm")
            else:
                ops.append(f"{a} 0 0 {d} {num(0, 50)} {num(0, 50)} cm")
            opened = True
        else:
            opened = False
        ops.append(f"{rnd.choice([0.5, 1, 2, 4])} w")
        kind = rnd.choice(["line", "poly", "curve", "rect", "closed"])
        if kind == "line":
            ops.append(f"{num()} {num()} m {num()} {num()} l S")
        elif kind == "poly":
            ops.append(f"{num()} {num()} m " + " ".join(f"{num()} {num()} l" for _ in range(rnd.randint(2, 4))) + " S")
        elif kind == "curve":
            ops.append(f"{num()} {num()} m {num()} {num()} {num()} {num()} {num()} {num()} c S")
        elif kind == "rect":
            ops.append(f"{num()} {num()} {num(5, 60)} {num(5, 60)} re {rnd.choice(['S', 'f', 'B'])}")
        else:
            ops.append(f"{num()} {num()} m {num()} {num()} l {num()} {num()} l h S")
        n_paths += 1
        if opened:
            ops.append("Q")
    return "\n".join(ops).encode()


def random_page(rnd: random.Random) -> PageSpec:
    kw = dict(size=(200, 120))
    if rnd.random() < 0.4:
        kw["rotate"] = rnd.choice([90, 180, 270])
    if rnd.random() < 0.3:
        kw["mediabox"] = (rnd.choice([-30, 0, 15]), rnd.choice([-10, 0, 20]), 200, 120)
        kw["mediabox"] = (kw["mediabox"][0], kw["mediabox"][1], kw["mediabox"][0] + 200, kw["mediabox"][1] + 120)
    return PageSpec(content=random_content(rnd), **kw)


@pytest.mark.parametrize("seed", range(60))
def test_primitives_agree_with_mupdf(seed):
    rnd = random.Random(seed)
    spec = random_page(rnd)
    data = build_pdf([spec])
    ours = ingest_pdf(data).pages[0]
    assert not ours.report.errors, ours.report.errors
    doc = pymupdf.open(stream=data, filetype="pdf")
    page = doc[0]
    msegs, mkeys, mwidths = mupdf_geometry(page)
    assert msegs, "oracle extracted nothing; generator bug"

    # size agreement (page geometry)
    assert (ours.geom.width, ours.geom.height) == pytest.approx((page.rect.width, page.rect.height), abs=1e-6)

    our_pts = [p for pr in ours.prims for p in pr.pts]
    # 1) every MuPDF key point (segment end, curve end, rect corner) exists in our output
    for k in mkeys:
        d = min(math.hypot(k[0] - p[0], k[1] - p[1]) for p in our_pts)
        assert d <= 2e-3, (seed, k, d)
    # 2) every vertex we emit lies on MuPDF's geometry (within flatten tol)
    for p in our_pts:
        d = min(seg_dist(p, a, b) for a, b in msegs)
        assert d <= TOL_POS, (seed, p, d)
    # 3) stroke widths agree as multisets (ours: stroked prims; MuPDF: stroked drawings merged per path)
    our_w = sorted({round(pr.stroke_w, 4) for pr in ours.prims if pr.stroke})
    assert set(our_w) >= set(mwidths) or all(any(abs(a - b) < 1e-3 for b in our_w) for a in mwidths)
