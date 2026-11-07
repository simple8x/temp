"""Exact rational geometry used as an independent oracle in tests (integer inputs).

Deliberately separate from ecci.arrangement: different algorithm (exact Fractions, no
tolerance, no spatial index) so the two cannot share a bug by construction.
"""

from __future__ import annotations

from fractions import Fraction as F


def cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def on_segment(p, a, b) -> bool:
    if cross(a, b, p) != 0:
        return False
    return min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])


def classify(a0, a1, b0, b1):
    """Exact classification of two non-degenerate integer segments.

    Returns one of:
      ("DISJOINT",)
      ("POINT", (x, y), kind)  kind in {"CROSS", "ENDPOINT_ENDPOINT", "T_A_ON_B", "T_B_ON_A"}
      ("OVERLAP", (p, q))      collinear overlap with positive length (p < q lexicographically)
    """
    d1 = cross(b0, b1, a0)
    d2 = cross(b0, b1, a1)
    d3 = cross(a0, a1, b0)
    d4 = cross(a0, a1, b1)
    if d1 == 0 and d2 == 0:  # collinear
        pts = sorted([a0, a1])
        qts = sorted([b0, b1])
        lo = max(pts[0], qts[0])
        hi = min(pts[1], qts[1])
        if lo > hi:
            return ("DISJOINT",)
        if lo == hi:
            kind = "ENDPOINT_ENDPOINT"
            return ("POINT", (F(lo[0]), F(lo[1])), kind)
        return ("OVERLAP", (lo, hi))
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        # proper crossing: solve exactly
        r = (a1[0] - a0[0], a1[1] - a0[1])
        s = (b1[0] - b0[0], b1[1] - b0[1])
        denom = r[0] * s[1] - r[1] * s[0]
        t = F((b0[0] - a0[0]) * s[1] - (b0[1] - a0[1]) * s[0], denom)
        return ("POINT", (a0[0] + t * r[0], a0[1] + t * r[1]), "CROSS")
    if d1 == 0 and on_segment(a0, b0, b1):
        p = a0
    elif d2 == 0 and on_segment(a1, b0, b1):
        p = a1
    elif d3 == 0 and on_segment(b0, a0, a1):
        p = b0
    elif d4 == 0 and on_segment(b1, a0, a1):
        p = b1
    else:
        return ("DISJOINT",)
    a_end = p in (a0, a1)
    b_end = p in (b0, b1)
    kind = "ENDPOINT_ENDPOINT" if (a_end and b_end) else ("T_A_ON_B" if a_end else "T_B_ON_A")
    return ("POINT", (F(p[0]), F(p[1])), kind)
