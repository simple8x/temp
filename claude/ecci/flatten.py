"""Cubic Bézier flattening with a rigorous, tested error bound.

Bound (FACT, proof sketch): a cubic lies in the convex hull of its control points, and the
distance to a segment is a convex function, so its maximum over the hull is attained at a
control point. With p0 and p3 on the segment, max over the curve of dist(curve, chord)
<= max(dist(p1, chord), dist(p2, chord)). The clamped projection onto the chord is
continuous from 0 to 1 along the curve, so every chord point is also within the same
bound of the curve: the Hausdorff distance between curve and chord is <= that maximum.
We subdivide (de Casteljau, t = 1/2) until the maximum is <= tol.
"""

from __future__ import annotations

import math

Point = tuple[float, float]


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0.0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _mid(a: Point, b: Point) -> Point:
    return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)


def flatten_cubic(
    p0: Point, p1: Point, p2: Point, p3: Point, tol: float, max_depth: int = 24
) -> tuple[list[Point], bool]:
    """Return (points after p0 up to and including p3, bound_guaranteed).

    bound_guaranteed is False only if max_depth was hit before reaching `tol`
    (the chord is still emitted, but the tolerance claim no longer holds).
    """
    if not (tol > 0 and math.isfinite(tol)):
        raise ValueError("tol must be a positive finite number")
    out: list[Point] = []
    guaranteed = True
    stack: list[tuple[Point, Point, Point, Point, int]] = [(p0, p1, p2, p3, 0)]
    while stack:
        a, b, c, d, depth = stack.pop()
        flat = max(point_segment_distance(b, a, d), point_segment_distance(c, a, d)) <= tol
        if flat:
            out.append(d)
            continue
        if depth >= max_depth:
            guaranteed = False
            out.append(d)
            continue
        ab, bc, cd = _mid(a, b), _mid(b, c), _mid(c, d)
        abc, bcd = _mid(ab, bc), _mid(bc, cd)
        m = _mid(abc, bcd)
        # push right half first so the left half is emitted first
        stack.append((m, bcd, cd, d, depth + 1))
        stack.append((a, ab, abc, m, depth + 1))
    return out, guaranteed
