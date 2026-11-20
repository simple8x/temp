import math

import pytest
from hypothesis import given, settings, strategies as st

from ecci.flatten import flatten_cubic, point_segment_distance


def bez(p0, p1, p2, p3, t):
    u = 1 - t
    return (u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
            u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1])


def dist_to_polyline(p, poly):
    return min(point_segment_distance(p, poly[i], poly[i + 1]) for i in range(len(poly) - 1))


def curve_to_poly_max(p0, p1, p2, p3, poly, n=2000):
    return max(dist_to_polyline(bez(p0, p1, p2, p3, i / n), poly) for i in range(n + 1))


def test_collinear_controls_give_single_segment():
    pts, ok = flatten_cubic((0, 0), (10, 0), (20, 0), (30, 0), 0.01)
    assert pts == [(30, 0)] and ok


def test_arc_like_curve_error_bound_both_directions():
    p0, p1, p2, p3 = (0, 0), (0, 55.2), (44.8, 100), (100, 100)
    tol = 0.05
    pts, ok = flatten_cubic(p0, p1, p2, p3, tol)
    poly = [p0] + pts
    assert ok and poly[-1] == p3 and len(poly) > 4
    assert curve_to_poly_max(p0, p1, p2, p3, poly) <= tol + 1e-9
    # reverse direction: polyline vertices lie on the curve; midpoints are within tol of it
    for i in range(len(poly) - 1):
        mid = ((poly[i][0] + poly[i + 1][0]) / 2, (poly[i][1] + poly[i + 1][1]) / 2)
        d = min(math.hypot(mid[0] - bez(p0, p1, p2, p3, k / 4000)[0],
                           mid[1] - bez(p0, p1, p2, p3, k / 4000)[1]) for k in range(4001))
        assert d <= tol + 1e-3   # 1e-3 = sampling slack of the oracle, not of the algorithm


def test_smaller_tolerance_never_gives_fewer_points():
    c = ((0, 0), (0, 80), (100, -20), (100, 60))
    counts = [len(flatten_cubic(*c, tol)[0]) for tol in (1.0, 0.1, 0.01, 0.001)]
    assert counts == sorted(counts) and counts[-1] > counts[0]


def test_closed_loop_curve_with_equal_endpoints_still_bounded():
    p0 = p3 = (0.0, 0.0)
    p1, p2 = (100.0, 100.0), (-100.0, 100.0)
    pts, ok = flatten_cubic(p0, p1, p2, p3, 0.05)
    assert ok and pts[-1] == p3
    assert curve_to_poly_max(p0, p1, p2, p3, [p0] + pts) <= 0.05 + 1e-9


def test_depth_limit_is_reported_not_hidden():
    pts, ok = flatten_cubic((0, 0), (0, 1000), (1000, 1000), (1000, 0), 1e-9, max_depth=2)
    assert ok is False and pts[-1] == (1000, 0)


@pytest.mark.parametrize("tol", [0, -1, float("nan"), float("inf")])
def test_invalid_tolerance_rejected(tol):
    with pytest.raises(ValueError):
        flatten_cubic((0, 0), (1, 1), (2, 2), (3, 3), tol)


coords = st.floats(min_value=-100, max_value=100, allow_nan=False, allow_infinity=False)
pt = st.tuples(coords, coords)


@settings(max_examples=200, deadline=None)
@given(pt, pt, pt, pt, st.sampled_from([0.01, 0.1, 1.0]))
def test_property_curve_within_tol_of_polyline(p0, p1, p2, p3, tol):
    pts, ok = flatten_cubic(p0, p1, p2, p3, tol)
    assert ok                                   # depth 24 is ample for |coords| <= 100
    assert pts[-1] == p3
    poly = [p0] + pts
    assert curve_to_poly_max(p0, p1, p2, p3, poly, n=300) <= tol + 1e-9
