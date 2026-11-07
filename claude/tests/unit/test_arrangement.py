import pytest

from ecci.arrangement import (ArrangementError, REL_INDEPENDENT, REL_SAME_PATH_OTHER,
                              REL_SAME_PATH_VERTEX, REL_SAME_SEGMENT, arm_relations,
                              build_arrangement, independent_prims_at)
from ecci.ir import Prim, Prov


def mk(pid, pts, closed=False, stroke=True, fill=False):
    kind = "point" if len(pts) == 1 else "polyline"
    prov = Prov(0, "VECTOR", (("page", pid),), 0, (0, 0, 0, 0), (f"vec:{pid}",))
    return Prim(pid, kind, [(float(x), float(y)) for x, y in pts], closed, stroke, fill, 1.0,
                None, None, None, "path", 0.05, True, False, prov)


def node_at(g, xy, tol=1e-6):
    hits = [n for n in g.nodes if abs(n.xy[0] - xy[0]) <= tol and abs(n.xy[1] - xy[1]) <= tol]
    assert len(hits) == 1, (xy, [n.xy for n in g.nodes])
    return hits[0]


def degree(g, xy):
    return len(node_at(g, xy).edges)


def test_proper_crossing_creates_degree4_node_with_through_facts():
    g = build_arrangement([mk(0, [(0, 5), (10, 5)]), mk(1, [(5, 0), (5, 10)])], eps=1e-6)
    n = node_at(g, (5, 5))
    assert len(n.edges) == 4 and n.vertices == () and set(n.through) == {(0, 0), (1, 0)}
    assert independent_prims_at(g, n.id) == {0, 1}
    assert len(g.nodes) == 5 and len(g.edges) == 4


def test_t_touch_splits_host_only():
    g = build_arrangement([mk(0, [(0, 5), (10, 5)]), mk(1, [(5, 5), (5, 10)])], eps=1e-6)
    n = node_at(g, (5, 5))
    assert len(n.edges) == 3 and n.vertices == ((1, 0),) and n.through == ((0, 0),)


def test_endpoint_to_endpoint_of_independent_prims():
    g = build_arrangement([mk(0, [(0, 0), (5, 0)]), mk(1, [(5, 0), (5, 5)])], eps=1e-6)
    n = node_at(g, (5, 0))
    assert len(n.edges) == 2 and set(n.vertices) == {(0, 1), (1, 0)} and n.through == ()
    (rel,) = arm_relations(g, n.id).values()
    assert rel == REL_INDEPENDENT                  # NOT same-path continuity


def test_same_path_vertex_is_continuity_not_coincidence():
    g = build_arrangement([mk(0, [(0, 0), (5, 0), (5, 5)])], eps=1e-6)
    n = node_at(g, (5, 0))
    (rel,) = arm_relations(g, n.id).values()
    assert rel == REL_SAME_PATH_VERTEX and independent_prims_at(g, n.id) == {0}


def test_closing_segment_wraparound_is_same_path_vertex():
    g = build_arrangement([mk(0, [(0, 0), (4, 0), (4, 4), (0, 4)], closed=True)], eps=1e-6)
    n = node_at(g, (0, 0))
    (rel,) = arm_relations(g, n.id).values()
    assert rel == REL_SAME_PATH_VERTEX             # segs 3 and 0 share vertex 0


def test_self_crossing_path_is_same_path_other_not_vertex():
    # a bow-tie polyline crossing itself at (5,5)
    g = build_arrangement([mk(0, [(0, 0), (10, 10), (10, 0), (0, 10)])], eps=1e-6)
    n = node_at(g, (5, 5))
    rels = set(arm_relations(g, n.id).values())
    assert len(n.edges) == 4 and rels <= {REL_SAME_PATH_OTHER, REL_SAME_SEGMENT}
    assert REL_SAME_PATH_VERTEX not in rels and independent_prims_at(g, n.id) == {0}


def test_halves_of_one_segment_through_a_t_are_same_segment():
    g = build_arrangement([mk(0, [(0, 5), (10, 5)]), mk(1, [(5, 5), (5, 10)])], eps=1e-6)
    rels = arm_relations(g, node_at(g, (5, 5)).id)
    kinds = sorted(rels.values())
    assert kinds == sorted([REL_SAME_SEGMENT, REL_INDEPENDENT, REL_INDEPENDENT])


def test_collinear_overlap_is_split_and_both_prims_cover_shared_edge():
    g = build_arrangement([mk(0, [(0, 0), (10, 0)]), mk(1, [(5, 0), (15, 0)])], eps=1e-6)
    shared = [e for e in g.edges if {r.prim_id for r in e.refs} == {0, 1}]
    assert len(shared) == 1
    a, b = g.nodes[shared[0].n0].xy, g.nodes[shared[0].n1].xy
    assert sorted([a, b]) == [(5.0, 0.0), (10.0, 0.0)]


def test_identical_duplicate_segments_share_one_edge():
    g = build_arrangement([mk(0, [(0, 0), (10, 0)]), mk(1, [(0, 0), (10, 0)])], eps=1e-6)
    assert len(g.edges) == 1 and {r.prim_id for r in g.edges[0].refs} == {0, 1}


def test_near_miss_just_outside_eps_is_not_merged_and_inside_is():
    far = build_arrangement([mk(0, [(0, 0), (5, 0)]), mk(1, [(5.0 + 2e-3, 0), (5, 5)])], eps=1e-3)
    assert len(far.nodes) == 4
    near = build_arrangement([mk(0, [(0, 0), (5, 0)]), mk(1, [(5.0 + 5e-4, 0), (5, 5)])], eps=1e-3)
    assert len(near.nodes) == 3


def test_endpoint_close_to_other_segment_interior_becomes_t():
    g = build_arrangement([mk(0, [(0, 5), (10, 5)]), mk(1, [(5, 5.0004), (5, 10)])], eps=1e-3)
    n = node_at(g, (5, 5), tol=1e-3)
    assert len(n.edges) == 3 and n.through == ((0, 0),)


def test_isolated_point_primitives_are_kept_aside():
    g = build_arrangement([mk(0, [(0, 0), (5, 0)]), mk(1, [(2, 0)])], eps=1e-6)
    assert g.points == [(1, (2.0, 0.0))] and len(g.nodes) == 2


def test_degenerate_segments_are_counted_and_skipped():
    g = build_arrangement([mk(0, [(0, 0), (1e-9, 0), (5, 0)])], eps=1e-6)
    assert g.stats["degenerate_segments"] == 1 and len(g.edges) == 1


def test_chaining_of_endpoints_is_transitive_and_visible_in_stats():
    # three endpoints each 0.9*eps apart in a row: A~B, B~C but A!~C -> one node by chaining
    eps = 1e-3
    prims = [mk(0, [(0, 0), (0, 5)]), mk(1, [(0.9 * eps, 0), (1, 1)]), mk(2, [(1.8 * eps, 0), (2, 2)])]
    g = build_arrangement(prims, eps=eps)
    assert len([n for n in g.nodes if abs(n.xy[1]) < 1e-9]) == 1


def test_empty_input_and_bad_eps():
    g = build_arrangement([], eps=1e-6)
    assert g.nodes == [] and g.edges == []
    for bad in (0, -1, float("nan")):
        with pytest.raises(ArrangementError):
            build_arrangement([], eps=bad)


def test_pair_limit_is_enforced():
    prims = [mk(i, [(0, i * 0.001), (100, i * 0.001 + 0.0005)]) for i in range(40)]
    with pytest.raises(ArrangementError) as e:
        build_arrangement(prims, eps=1e-6, max_pairs=10)
    assert e.value.code == "PAIR_LIMIT"


def test_output_is_deterministic_and_input_order_independent():
    prims = [mk(0, [(0, 5), (10, 5)]), mk(1, [(5, 0), (5, 10)]), mk(2, [(2, 2), (8, 8)])]
    a = build_arrangement(prims, eps=1e-6).to_dict()
    b = build_arrangement(prims, eps=1e-6).to_dict()
    assert a == b
    rev = build_arrangement([mk(2, [(2, 2), (8, 8)]), mk(1, [(5, 0), (5, 10)]), mk(0, [(0, 5), (10, 5)])],
                            eps=1e-6)
    pts = lambda g: sorted(n.xy for n in g.nodes)
    assert pts(rev) == pts(build_arrangement(prims, eps=1e-6))
    assert len(rev.edges) == len(a["edges"])
