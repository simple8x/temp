"""S1 evidence: match a body cluster against library symbols (body graphics only).

The template model deliberately EXCLUDES pin lines: S2 (measured stubs) must stay an
independent information source (different primitives, see evidence lineage).

Transform = one of the 8 symmetries of the square (rotations and mirrors) composed with a
uniform scale and a translation. The scale/translation come from aligning bounding boxes; the
transform is accepted only if both residuals are within tolerance:
  r1 = max distance of every cluster vertex to the transformed template outline,
  r2 = max distance of every template corner to the cluster outline,
  r3 = coverage: template samples -> cluster outline and cluster segment midpoints -> template.
There is no 'best match wins': ALL accepted (symbol, orientation) hits are kept and
`resolve` reports ambiguity instead of guessing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .flatten import point_segment_distance
from .kicad_lib import SymbolDef
from .symbols import Cluster

D4 = (
    (1, 0, 0, 1), (0, -1, 1, 0), (-1, 0, 0, -1), (0, 1, -1, 0),
    (-1, 0, 0, 1), (1, 0, 0, -1), (0, 1, 1, 0), (0, -1, -1, 0),
)
DIM_EPS = 1e-9
COVER_SAMPLES = 5


@dataclass(frozen=True)
class MatchParams:
    tau_rel: float = 0.02
    tau_abs: float = 0.05
    scale_isotropy: float = 0.03       # |sx - sy| / s allowed
    min_scale: float = 1e-6


@dataclass(frozen=True)
class Hit:
    symbol: SymbolDef
    orient: int
    scale: float
    tx: float
    ty: float
    residual: float

    def place(self, x: float, y: float) -> tuple[float, float]:
        a, b, c, d = D4[self.orient]
        return (self.scale * (a * x + b * y) + self.tx, self.scale * (c * x + d * y) + self.ty)


def tolerance(cl: Cluster, p: MatchParams) -> float:
    w, h = cl.bbox[2] - cl.bbox[0], cl.bbox[3] - cl.bbox[1]
    return p.tau_rel * math.hypot(w, h) + p.tau_abs


def _tf(m, pt):
    a, b, c, d = m
    return (a * pt[0] + b * pt[1], c * pt[0] + d * pt[1])


def match_cluster(cl: Cluster, symbols, params: MatchParams = MatchParams()) -> list[Hit]:
    tau = tolerance(cl, params)
    cw, ch = cl.bbox[2] - cl.bbox[0], cl.bbox[3] - cl.bbox[1]
    hits: list[Hit] = []
    for sym in symbols:
        tsegs = sym.segments()
        if not tsegs:
            continue
        allpts = [p for s in tsegs for p in s]
        for oi, m in enumerate(D4):
            pts = [_tf(m, p) for p in allpts]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            tw, th = x1 - x0, y1 - y0
            if tw > DIM_EPS and th > DIM_EPS:
                sx, sy = cw / tw, ch / th
                s = 0.5 * (sx + sy)
                if s <= params.min_scale or abs(sx - sy) > params.scale_isotropy * s:
                    continue
            elif tw <= DIM_EPS and th > DIM_EPS:
                s = ch / th
                if s <= params.min_scale or cw > tau:
                    continue
            elif th <= DIM_EPS and tw > DIM_EPS:
                s = cw / tw
                if s <= params.min_scale or ch > tau:
                    continue
            else:
                continue
            tx, ty = cl.bbox[0] - s * x0, cl.bbox[1] - s * y0
            segs_t = [((s * _tf(m, a)[0] + tx, s * _tf(m, a)[1] + ty),
                       (s * _tf(m, b)[0] + tx, s * _tf(m, b)[1] + ty)) for a, b in tsegs]
            r1 = max(min(point_segment_distance(v, a, b) for a, b in segs_t) for v in cl.verts)
            if r1 > tau:
                continue
            corners = [(s * _tf(m, c)[0] + tx, s * _tf(m, c)[1] + ty) for c in sym.corners()]
            r2 = max((min(point_segment_distance(c, a, b) for a, b in cl.segs) for c in corners), default=0.0)
            if r2 > tau:
                continue
            # coverage: points sampled along every template segment must lie on the cluster
            # outline, and the midpoint of every cluster segment on the template outline
            # (vertex checks alone accept mirrored shapes whose edges differ).
            r3 = 0.0
            for a, b in segs_t:
                for k in range(1, COVER_SAMPLES + 1):
                    f = k / (COVER_SAMPLES + 1)
                    q = (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]))
                    r3 = max(r3, min(point_segment_distance(q, c, d) for c, d in cl.segs))
                if r3 > tau:
                    break
            if r3 > tau:
                continue
            for c, d in cl.segs:
                q = (0.5 * (c[0] + d[0]), 0.5 * (c[1] + d[1]))
                r3 = max(r3, min(point_segment_distance(q, a, b) for a, b in segs_t))
            if r3 > tau:
                continue
            hits.append(Hit(sym, oi, s, tx, ty, max(r1, r2, r3)))
    return hits


@dataclass(frozen=True)
class ResolvedPin:
    tip: tuple[float, float]
    base: tuple[float, float]
    numbers: tuple[str, ...]          # all pin numbers any accepted hit assigns to this tip


@dataclass(frozen=True)
class Resolution:
    hits: tuple[Hit, ...]
    status: str                       # "NONE" | "UNIQUE" | "DIFFERENT_TIP_SETS"
    id_state: str                     # "CONFIRMED" | "AMBIGUOUS" | "UNKNOWN"
    pins: tuple[ResolvedPin, ...]
    symbols: tuple[str, ...]
    note: str = ""


def _tips(h: Hit):
    return [(p.number, h.place(*p.tip), h.place(*p.base)) for p in h.symbol.pins]


def _same_set(a, b, tau: float) -> bool:
    if len(a) != len(b):
        return False
    used: set[int] = set()
    for _, ta, _ in a:
        j = next((j for j, (_, tb, _) in enumerate(b)
                  if j not in used and math.hypot(ta[0] - tb[0], ta[1] - tb[1]) <= tau), None)
        if j is None:
            return False
        used.add(j)
    return True


def resolve(cl: Cluster, hits: list[Hit], params: MatchParams = MatchParams()) -> Resolution:
    if not hits:
        return Resolution((), "NONE", "UNKNOWN", (), (), "no template accepted")
    tau = tolerance(cl, params)
    sets = [_tips(h) for h in hits]
    names = tuple(sorted({h.symbol.name for h in hits}))
    first = sets[0]
    if not all(_same_set(first, s, tau) for s in sets[1:]):
        return Resolution(tuple(hits), "DIFFERENT_TIP_SETS", "AMBIGUOUS", (), names,
                          "accepted templates predict different pin positions")
    if not first:
        return Resolution(tuple(hits), "UNIQUE", "UNKNOWN", (), names, "template has no pins")
    # numbers per tip across all hits; id is CONFIRMED only if every hit agrees on every number
    pins: list[ResolvedPin] = []
    agree = True
    for num0, tip0, base0 in first:
        nums = set()
        for s in sets:
            for n, t, _ in s:
                if math.hypot(t[0] - tip0[0], t[1] - tip0[1]) <= tau:
                    nums.add(n)
        if nums != {num0}:
            agree = False
        pins.append(ResolvedPin(tip0, base0, tuple(sorted(nums))))
    return Resolution(tuple(hits), "UNIQUE", "CONFIRMED" if agree else "AMBIGUOUS", tuple(pins), names,
                      "" if agree else "symmetric template: pin numbers cannot be assigned from geometry alone")
