"""Property tests for body-cluster geometry helpers.

`strip_stubs` is indexed for speed (a KiCad sheet has clusters with thousands of primitives); the plain-loop
`strip_stubs_reference` states the same test. These properties check the two never disagree.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st


# ---- strip_stubs: the grid-indexed implementation must agree with the plain-loop statement of the same test
@given(st.lists(st.tuples(st.integers(0, 60), st.integers(0, 60), st.integers(0, 60), st.integers(0, 60),
                          st.sampled_from([0.2, 0.45, 0.9])), min_size=1, max_size=14),
       st.lists(st.integers(0, 13), max_size=6))
@settings(max_examples=200, deadline=None)
def test_strip_stubs_matches_the_reference_implementation(lines, duplicate):
    # `duplicate` re-plots some of the lines at the same coordinates: a primitive never counts as touching
    # another copy of ITSELF, so these inputs exercise the geometry-key part of the test
    lines = list(lines) + [lines[k % len(lines)] for k in duplicate] if lines else lines
    from ecci.symbols import Cluster, strip_stubs, strip_stubs_reference

    class P:
        def __init__(self, i, pts, w):
            self.id, self.pts, self.stroke_w, self.line_closed, self.kind = i, pts, w, False, "polyline"

        def segments(self):
            return [(0, self.pts[0], self.pts[1])]

    prims = {}
    for k, (x0, y0, x1, y1, w) in enumerate(lines):
        if (x0, y0) == (x1, y1):
            continue
        prims[k] = P(k, ((float(x0), float(y0)), (float(x1), float(y1))), w)
    if not prims:
        return
    verts = [v for p in prims.values() for v in p.pts]
    xs, ys = [v[0] for v in verts], [v[1] for v in verts]
    segs = [(p.pts[0], p.pts[1]) for p in prims.values()]
    cl = Cluster(0, tuple(sorted(prims)), (min(xs), min(ys), max(xs), max(ys)), tuple(verts), tuple(segs))
    fast, ref = strip_stubs(cl, prims), strip_stubs_reference(cl, prims)
    assert (fast is None) == (ref is None)
    if fast is not None:
        assert fast[1] == ref[1] and fast[0].prim_ids == ref[0].prim_ids


def test_strip_stubs_finds_a_touch_across_a_grid_cell_boundary():
    """A contact within the stroke tolerance but on the other side of a grid cell boundary must still be seen:
    the grid margin has to cover the largest tolerance, not just eps."""
    from ecci.symbols import Cluster, strip_stubs, strip_stubs_reference

    class P:
        def __init__(self, i, pts, w, closed=False):
            self.id, self.pts, self.stroke_w, self.line_closed, self.kind = i, pts, w, closed, "polyline"

        def segments(self):
            n = len(self.pts)
            segs = [(k, self.pts[k], self.pts[k + 1]) for k in range(n - 1)]
            if self.line_closed and n >= 3:
                segs.append((n - 1, self.pts[-1], self.pts[0]))
            return segs

    # body outline ends 0.3 short of x=20.0 (a cell boundary at cell=20); the stub starts exactly there, so the
    # contact is within the stroke tolerance (0.92) but in the next cell
    body = P(0, ((5.0, -5.0), (19.7, -5.0), (19.7, 5.0), (5.0, 5.0)), 0.9, closed=True)
    stub = P(1, ((20.0, 0.0), (26.0, 0.0)), 0.9)
    prims = {0: body, 1: stub}
    verts = [v for p in prims.values() for v in p.pts]
    xs, ys = [v[0] for v in verts], [v[1] for v in verts]
    segs = tuple((a, b) for p in prims.values() for _, a, b in p.segments())
    cl = Cluster(0, (0, 1), (min(xs), min(ys), max(xs), max(ys)), tuple(verts), segs)
    ref = strip_stubs_reference(cl, prims)
    fast = strip_stubs(cl, prims)
    assert ref is not None and ref[1] == (1,)            # the reference sees the contact
    assert fast is not None and fast[1] == ref[1] and fast[0].prim_ids == ref[0].prim_ids
