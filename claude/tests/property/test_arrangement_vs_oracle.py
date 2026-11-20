"""Property tests: arrangement facts vs an exact rational oracle (tests/oracle.py).

Inputs are small integer coordinates so that touches, T-junctions, overlaps and crossings
occur constantly. eps is far below the lattice spacing, so within these inputs the exact
oracle and a tolerance-based arrangement must agree.
"""

from __future__ import annotations

from itertools import combinations

from hypothesis import HealthCheck, given, settings, strategies as st

from ecci.arrangement import REL_SAME_PATH_VERTEX, arm_relations, build_arrangement
from ecci.ir import Prim, Prov
from oracle import classify

EPS = 1e-6
G = 6
coord = st.integers(0, G)
point = st.tuples(coord, coord)


def mk(pid, pts, closed=False):
    prov = Prov(0, "VECTOR", (("page", pid),), 0, (0, 0, 0, 0), (f"vec:{pid}",))
    return Prim(pid, "polyline", [(float(x), float(y)) for x, y in pts], closed, True, False, 1.0,
                None, None, None, "path", 0.05, True, False, prov)


@st.composite
def polylines(draw, max_prims=5):
    n = draw(st.integers(1, max_prims))
    out = []
    for pid in range(n):
        k = draw(st.integers(2, 4))
        pts = [draw(point)]
        for _ in range(k - 1):
            nxt = draw(point)
            if nxt != pts[-1]:
                pts.append(nxt)
        if len(pts) >= 2:
            closed = draw(st.booleans()) and len(pts) >= 3 and pts[0] != pts[-1]
            out.append((pid, pts, closed))
    return out


def seg_list(specs):
    segs = []
    for pid, pts, closed in specs:
        n = len(pts)
        for i in range(n - 1):
            segs.append((pid, i, pts[i], pts[i + 1]))
        if closed and n >= 3:
            segs.append((pid, n - 1, pts[n - 1], pts[0]))
    return segs


def find_node(g, xy):
    hits = [n for n in g.nodes if abs(n.xy[0] - float(xy[0])) <= 1e-5 and abs(n.xy[1] - float(xy[1])) <= 1e-5]
    return hits


def build(specs):
    return build_arrangement([mk(pid, pts, closed) for pid, pts, closed in specs], eps=EPS)


SETTINGS = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@SETTINGS
@given(polylines())
def test_pairwise_facts_match_exact_oracle(specs):
    g = build(specs)
    segs = seg_list(specs)
    for (pa, ia, a0, a1), (pb, ib, b0, b1) in combinations(segs, 2):
        if (pa, ia) == (pb, ib):
            continue
        res = classify(a0, a1, b0, b1)
        if res[0] == "DISJOINT":
            continue
        if res[0] == "OVERLAP":
            for end in res[1]:
                (n,) = find_node(g, end)
                incident = {(r.prim_id, r.seg_idx) for eid in n.edges for r in g.edges[eid].refs}
                # both segments must own an edge piece incident to each end of the overlap
                assert (pa, ia) in incident and (pb, ib) in incident, (end, incident)
            continue
        _, (x, y), kind = res
        hits = find_node(g, (float(x), float(y)))
        assert len(hits) == 1, (kind, (x, y))
        n = hits[0]
        verts = set(n.vertices)
        thru = set(n.through)
        if kind == "CROSS":
            assert (pa, ia) in thru and (pb, ib) in thru
        elif kind == "T_A_ON_B":
            assert (pb, ib) in thru and any(v[0] == pa for v in verts)
        elif kind == "T_B_ON_A":
            assert (pa, ia) in thru and any(v[0] == pb for v in verts)
        else:  # ENDPOINT_ENDPOINT
            assert any(v[0] == pa for v in verts) and any(v[0] == pb for v in verts)


@SETTINGS
@given(polylines())
def test_every_segment_is_covered_exactly_once_from_t0_to_t1(specs):
    g = build(specs)
    cover: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for e in g.edges:
        for r in e.refs:
            cover.setdefault((r.prim_id, r.seg_idx), []).append((r.t0, r.t1))
    for pid, i, p, q in seg_list(specs):
        pieces = sorted(cover.get((pid, i), []))
        assert pieces, (pid, i)
        assert abs(pieces[0][0]) < 1e-9 and abs(pieces[-1][1] - 1.0) < 1e-9
        for (a0, a1), (b0, b1) in zip(pieces, pieces[1:]):
            assert abs(a1 - b0) < 1e-9            # contiguous, no gap
        assert all(t1 > t0 for t0, t1 in pieces)  # no zero-length / overlapping piece


@SETTINGS
@given(polylines())
def test_edge_endpoints_lie_on_their_source_segments(specs):
    g = build(specs)
    by_seg = {(pid, i): (p, q) for pid, i, p, q in seg_list(specs)}
    for e in g.edges:
        for r in e.refs:
            (px, py), (qx, qy) = by_seg[(r.prim_id, r.seg_idx)]
            for node_id, t in ((e.n0, None), (e.n1, None)):
                x, y = g.nodes[node_id].xy
                # distance from node to the infinite line through the source segment
                dx, dy = qx - px, qy - py
                dist = abs(dx * (y - py) - dy * (x - px)) / (dx * dx + dy * dy) ** 0.5
                assert dist <= 1e-5


@SETTINGS
@given(polylines(), st.randoms(use_true_random=False))
def test_result_does_not_depend_on_primitive_order(specs, rnd):
    base = build(specs)
    shuffled = list(specs)
    rnd.shuffle(shuffled)
    # keep original ids so only the ORDER changes
    other = build_arrangement([mk(pid, pts, closed) for pid, pts, closed in shuffled], eps=EPS)
    nodes = lambda g: sorted((round(n.xy[0], 6), round(n.xy[1], 6)) for n in g.nodes)
    edges = lambda g: sorted((tuple(sorted((g.nodes[e.n0].xy, g.nodes[e.n1].xy))),
                              tuple(sorted((r.prim_id, r.seg_idx) for r in e.refs))) for e in g.edges)
    assert nodes(base) == nodes(other)
    assert edges(base) == edges(other)


@SETTINGS
@given(polylines())
def test_same_path_vertex_is_never_claimed_between_distinct_prims(specs):
    g = build(specs)
    edges = {e.id: e for e in g.edges}
    for n in g.nodes:
        for (e1, e2), rel in arm_relations(g, n.id).items():
            if rel == REL_SAME_PATH_VERTEX:
                shared = {r.prim_id for r in edges[e1].refs} & {r.prim_id for r in edges[e2].refs}
                assert shared                      # continuity needs a common primitive
                # and the polyline vertex really sits at this node
                assert any(v[0] in shared for v in n.vertices)


@SETTINGS
@given(st.lists(point, min_size=3, max_size=3), st.integers(0, 6))
def test_two_independent_prims_sharing_an_endpoint_are_never_same_path(tri, extra):
    a, b, c = tri
    if len({a, b, c}) < 3:
        return
    g = build_arrangement([mk(0, [a, b]), mk(1, [b, c])], eps=EPS)
    for n in g.nodes:
        for rel in arm_relations(g, n.id).values():
            if rel == REL_SAME_PATH_VERTEX:
                # only possible if both arms belong to the same primitive: impossible here,
                # unless collinear overlap makes one edge shared by both prims
                edges = {e.id: e for e in g.edges}
                for (e1, e2), r in arm_relations(g, n.id).items():
                    if r == REL_SAME_PATH_VERTEX:
                        s1 = {x.prim_id for x in edges[e1].refs}
                        s2 = {x.prim_id for x in edges[e2].refs}
                        assert s1 & s2
