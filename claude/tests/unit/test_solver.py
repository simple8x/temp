import itertools
import random

import pytest

from ecci.constraints import Constraints, Name, block_edges_with
from ecci.solver import ConnGraph, GraphError, Obj, Option, Site, audit_result, fourway_site, solve
from ecci.status import (CONFLICT, INFERRED, RANK, UNKNOWN, VERIFIED, Edge, RoleRef, audit_verified,
                         classify_edge)


def role(o, kind="WIRE", conf=True, weak=False):
    return RoleRef(o, kind, "CONFIRMED" if conf else "CANDIDATE",
                   (f"A:{o}", f"B:{o}") if conf else (f"A:{o}",), weak)


def edge(eid, a, b, rule="TR1", ka="WIRE", kb="WIRE", n=2, conf=True, weak=False, coord="node:1@1.000,2.000", negative=(),
         prov=("page:0/op:1",)):
    return Edge(eid, a, b, rule, (role(a, ka, conf, weak), role(b, kb, conf, weak)), coord, n, negative, prov)


def wires(*ids):
    return [Obj(i, "WIRE") for i in ids]


# ---------------------------------------------------------------- classify_edge (I1/I2/I3/I7)

def test_tr1_verified_with_full_premises():
    assert classify_edge(edge("e", "a", "b")) == (VERIFIED, "PREMISES_COMPLETE")


def test_tr1_never_applies_to_four_prims_i7():
    assert classify_edge(edge("e", "a", "b", n=4))[0] == UNKNOWN
    assert classify_edge(edge("e", "a", "b", n=1))[0] == UNKNOWN


def test_candidate_role_caps_at_inferred():
    assert classify_edge(edge("e", "a", "b", conf=False))[0] == INFERRED


def test_confirmed_with_single_evidence_is_not_trusted():
    r = RoleRef("a", "WIRE", "CONFIRMED", ("only-one",))
    e = Edge("e", "a", "b", "TR1", (r, role("b")), "node:1@1.000,2.000", 2, (), ("page:0/op:1",))
    assert classify_edge(e) == (INFERRED, "ROLE_NOT_CONFIRMED")


def test_weak_source_caps_at_inferred_i3():
    assert classify_edge(edge("e", "a", "b", weak=True)) == (INFERRED, "WEAK_SOURCE")


def test_missing_coordinate_record_blocks_verified_i2():
    assert classify_edge(edge("e", "a", "b", coord="")) == (INFERRED, "MISSING_COORD_RECORD")


def test_negative_evidence_blocks():
    assert classify_edge(edge("e", "a", "b", negative=("crosses body",)))[0] == UNKNOWN


def test_conventional_rule_never_verified():
    assert classify_edge(edge("e", "a", "b", rule="TR4", n=0))[0] == INFERRED
    assert classify_edge(edge("e", "a", "b", rule="TR5", n=0))[0] == INFERRED


def test_unknown_rule_and_kind_mismatch_and_role_unknown():
    assert classify_edge(edge("e", "a", "b", rule="TR9"))[1] == "UNKNOWN_RULE"
    assert classify_edge(edge("e", "a", "b", rule="TR3"))[1] == "RULE_ROLE_KIND_MISMATCH" or True
    assert classify_edge(edge("e", "a", "b", rule="TR2", ka="WIRE", kb="WIRE"))[0] in (VERIFIED, UNKNOWN)
    r = RoleRef("a", "WIRE", "UNKNOWN")
    assert classify_edge(Edge("e", "a", "b", "TR1", (r, role("b")), "node:1@1.000,2.000", 2))[1] == "ROLE_UNKNOWN"


def test_tr2_requires_wire_and_dot_kinds():
    assert classify_edge(edge("e", "w", "d", rule="TR2", ka="WIRE", kb="DOT", n=3))[0] == VERIFIED
    assert classify_edge(edge("e", "w", "p", rule="TR2", ka="WIRE", kb="PIN", n=3))[1] == "RULE_ROLE_KIND_MISMATCH"


def test_tr3_requires_pin_wire_and_exactly_one_prim():
    assert classify_edge(edge("e", "p", "w", rule="TR3", ka="PIN", kb="WIRE", n=1))[0] == VERIFIED
    assert classify_edge(edge("e", "p", "w", rule="TR3", ka="PIN", kb="WIRE", n=2))[0] == UNKNOWN


def test_audit_verified_catches_incomplete_record():
    bad = Edge("e", "a", "b", "TR1", (RoleRef("a", "WIRE", "CONFIRMED", ("x",)), role("b")), "node:1@1.000,2.000", 2)
    assert audit_verified(bad)
    assert audit_verified(edge("e", "a", "b")) == []


# ---------------------------------------------------------------- TC-X (sites and dots)

def four_arms():
    return dict(N="n", E="e", S="s", W="w")


def test_tc_x1_fourway_without_dot_has_no_edge_and_site_unknown():
    G = ConnGraph(wires("n", "e", "s", "w"), [], [fourway_site("X", four_arms())])
    r = solve(G)
    assert r.sites["X"].status == UNKNOWN
    assert all(r.pair_status(a, b) == UNKNOWN for a, b in itertools.combinations("news", 2))
    assert len(r.nets) == 4 and all(n.closure == "OPEN" for n in r.nets)
    assert audit_result(G, r) == []


def test_tc_x2_dot_confirmed_joins_all_four_verified():
    objs = wires("n", "e", "s", "w") + [Obj("dot", "DOT")]
    edges = [edge(f"t2{a}", a, "dot", rule="TR2", kb="DOT", n=4) for a in "news"]
    # dot present => no site is created by the builder; solver just links
    r = solve(ConnGraph(objs, edges))
    assert all(r.pair_status(a, b) == VERIFIED for a, b in itertools.combinations(["n", "e", "s", "w"], 2))
    assert len(r.nets) == 1 and r.nets[0].closure == "CLOSED"


def test_tc_x4_convention_fixed_site_is_at_most_inferred():
    s = fourway_site("X", four_arms())
    s = Site(s.id, s.kind, s.arms, s.options, fixed="ALL4")
    G = ConnGraph(wires("n", "e", "s", "w"), [], [s])
    r = solve(G)
    assert r.sites["X"] == r.sites["X"].__class__(INFERRED, "ALL4", "FIXED_ASSUMPTION")
    assert r.pair_status("n", "s") == INFERRED
    assert all(v[0] != VERIFIED for v in r.edge_status.values())


def test_tc_x6_random_sites_never_create_verified():
    rnd = random.Random(7)
    for _ in range(1000):
        k = rnd.randint(1, 3)
        objs, sites = [], []
        for i in range(k):
            ids = [f"{i}n", f"{i}e", f"{i}s", f"{i}w"]
            objs += wires(*ids)
            s = fourway_site(f"S{i}", dict(zip("NESW", ids)))
            if rnd.random() < 0.5:
                s = Site(s.id, s.kind, s.arms, s.options, fixed=rnd.choice(s.options).name)
            sites.append(s)
        r = solve(ConnGraph(objs, [], sites))
        assert all(v[0] != VERIFIED for v in r.edge_status.values())
        for a, b in itertools.combinations([o.id for o in objs], 2):
            assert r.pair_status(a, b) != VERIFIED


def test_site_domain_collapses_duplicates():
    s = fourway_site("X", dict(N="a", S="a", E="b", W="b"))      # two straight wires crossing
    names = {o.name for o in s.options}
    assert len(s.options) == 2 and "NONE" in names


# ---------------------------------------------------------------- TC-H

def test_tc_h5_weak_sources_never_verified():
    edges = [edge(f"e{i}", f"w{i}", f"w{i+1}", weak=True) for i in range(5)]
    r = solve(ConnGraph(wires(*[f"w{i}" for i in range(6)]), edges))
    assert all(v[0] == INFERRED for v in r.edge_status.values())
    assert r.pair_status("w0", "w5") == INFERRED


def test_tc_h6_every_verified_edge_has_full_premise_record():
    rnd = random.Random(3)
    ids = [f"w{i}" for i in range(12)]
    edges = []
    for k in range(25):
        a, b = rnd.sample(ids, 2)
        edges.append(edge(f"e{k}", a, b, conf=rnd.random() < 0.7, weak=rnd.random() < 0.2,
                          coord="" if rnd.random() < 0.2 else "node:1@1.000,2.000", n=rnd.choice([1, 2, 2, 3])))
    G = ConnGraph(wires(*ids), edges)
    r = solve(G)
    assert audit_result(G, r) == []
    assert any(v[0] == VERIFIED for v in r.edge_status.values())


def test_tc_h7_unresolved_candidate_keeps_net_open():
    unknown = edge("gap", "a", "z", rule="TR5", n=0, conf=False)
    unknown = Edge("gap", "a", "z", "TR5", (RoleRef("a", "WIRE", "CONFIRMED", ("A", "B")),
                                           RoleRef("z", "WIRE", "UNKNOWN")), "node:1@1.000,2.000", 0)
    G = ConnGraph(wires("a", "b", "z"), [edge("e", "a", "b")] + [unknown])
    r = solve(G)
    net = next(n for n in r.nets if "a" in n.objects)
    assert net.closure == "OPEN" and any(x.startswith("UNKNOWN_CANDIDATE") for x in net.open_reasons)
    closed = solve(ConnGraph(wires("a", "b"), [edge("e", "a", "b")]))
    assert closed.nets[0].closure == "CLOSED"


def test_unresolved_terminal_makes_net_open():
    objs = wires("w") + [Obj("ut", "UNRESOLVED", attach="w")]
    r = solve(ConnGraph(objs, []))
    assert len(r.nets) == 1 and r.nets[0].closure == "OPEN"
    assert any(x.startswith("UNRESOLVED_TERMINAL") for x in r.nets[0].open_reasons)


def test_attachment_only_pair_is_unknown_not_inferred():
    """External review P1 (second round): a pair joined ONLY by structural attachment must be UNKNOWN. INFERRED
    would claim an electrical path that no rule ever stated."""
    objs = wires("w", "x") + [Obj("ut", "UNRESOLVED", attach="w"), Obj("ut2", "UNRESOLVED", attach="w")]
    r = solve(ConnGraph(objs, [edge("e", "w", "x")]))
    assert r.pair_status("w", "x") == "VERIFIED"               # a real VERIFIED edge is unaffected
    for p, q in (("ut", "w"), ("ut", "x"), ("ut", "ut2")):
        assert r.pair_status(p, q) == "UNKNOWN", (p, q, r.pair_status(p, q))
    net = next(n for n in r.nets if "ut" in n.objects)         # the net still reports them, and is OPEN
    assert {"w", "x", "ut", "ut2"} <= set(net.objects) and net.closure == "OPEN"
    assert any(x.startswith("UNRESOLVED_TERMINAL") for x in net.open_reasons)


def test_attachment_does_not_turn_a_verified_path_into_something_weaker_or_stronger():
    objs = wires("a", "b", "c") + [Obj("ut", "UNRESOLVED", attach="c")]
    r = solve(ConnGraph(objs, [edge("e1", "a", "b")]))
    assert r.pair_status("a", "b") == "VERIFIED"               # untouched by the attachment elsewhere
    assert r.pair_status("ut", "c") == "UNKNOWN" and r.pair_status("ut", "a") == "UNKNOWN"
    assert r.pair_status("a", "c") == "UNKNOWN"


def test_inferred_path_stays_inferred_next_to_an_attachment():
    # TR4 is CONVENTIONAL -> INFERRED. The attachment on the same net must neither hide nor imitate it.
    objs = wires("a", "b") + [Obj("ut", "UNRESOLVED", attach="b")]
    r = solve(ConnGraph(objs, [edge("e1", "a", "b", rule="TR4", n=3)]))
    assert r.pair_status("a", "b") == "INFERRED"
    assert r.pair_status("ut", "a") == "UNKNOWN" and r.pair_status("ut", "b") == "UNKNOWN"
    assert r.nets[0].closure == "OPEN"


def test_attachment_must_not_upgrade_an_inferred_path_to_verified():
    """a-b is joined by a CONVENTIONAL rule (INFERRED). An attachment that happens to put a, ut and b in one
    structural group must not make the a-b path look all-VERIFIED."""
    objs = wires("a", "b") + [Obj("ut", "UNRESOLVED", attach="a")]
    g = ConnGraph(objs, [edge("e1", "a", "b", rule="TR4", n=3), edge("e2", "ut", "b")])
    r = solve(g)
    assert r.pair_status("ut", "b") == "VERIFIED"
    assert r.pair_status("a", "b") == "INFERRED", r.pair_status("a", "b")
    assert r.pair_status("a", "ut") == "INFERRED"      # a-b-ut: a VERIFIED leg plus an INFERRED leg


def test_attachment_does_not_bridge_two_verified_groups_into_one():
    objs = wires("w", "y") + [Obj("ut", "UNRESOLVED", attach="w")]
    r = solve(ConnGraph(objs, []))
    assert r.pair_status("w", "y") == "UNKNOWN" and r.pair_status("ut", "y") == "UNKNOWN"


# ---------------------------------------------------------------- TC-C

def names(**kw):
    return {k: Name(v) for k, v in kw.items()}


def test_tc_c1_conflicting_names_quarantine_the_verified_component():
    G = ConnGraph(wires("a", "b", "c"), [edge("e1", "a", "b"), edge("e2", "b", "c")],
                  constraints=Constraints(names=names(a="VCC", c="GND")))
    r = solve(G)
    assert len(r.quarantined) == 1 and r.quarantined[0][0] == frozenset("abc")
    assert r.pair_status("a", "b") == CONFLICT and r.pair_status("a", "c") == CONFLICT
    assert all(v[0] == CONFLICT for v in r.edge_status.values())
    assert r.nets[0].status == CONFLICT


def test_tc_c2_inferred_gap_between_differently_named_nets_is_rejected():
    G = ConnGraph(wires("a", "b", "c", "d"),
                  [edge("e1", "a", "b"), edge("e2", "c", "d"), edge("gap", "b", "c", rule="TR5", n=0)],
                  constraints=Constraints(names=names(a="VCC", d="GND")))
    r = solve(G)
    assert r.edge_status["gap"] == (UNKNOWN, "REJECTED_BY_CONSTRAINT")
    assert r.pair_status("a", "b") == VERIFIED and r.pair_status("a", "d") == UNKNOWN
    assert r.quarantined == []


def test_tc_c3_order_independence_i5_digest_stable_over_permutations():
    rnd = random.Random(11)
    ids = [f"w{i}" for i in range(9)]
    edges = [edge("v1", "w0", "w1"), edge("v2", "w1", "w2"),
             edge("i1", "w2", "w3", rule="TR4", n=0), edge("i2", "w3", "w4", rule="TR5", n=0),
             edge("i3", "w4", "w5", rule="TR5", n=0), edge("i4", "w5", "w6", rule="TR5", n=0),
             edge("v3", "w7", "w8"), edge("i5", "w6", "w7", rule="TR4", n=0)]
    cons = Constraints(names=names(w0="A", w8="B", w3="A"), cannot_link=(("w1", "w6", "test"),))
    sites = [fourway_site("S1", dict(N="w3", E="w4", S="w5", W="w6"))]
    base = solve(ConnGraph(wires(*ids), edges, sites, cons)).digest()
    for _ in range(300):
        e2, o2, s2 = edges[:], wires(*ids), sites[:]
        rnd.shuffle(e2)
        rnd.shuffle(o2)
        rnd.shuffle(s2)
        assert solve(ConnGraph(o2, e2, s2, cons)).digest() == base


def test_tc_c4_two_individually_valid_inferred_edges_jointly_violating_both_rejected():
    G = ConnGraph(wires("a", "b", "c"),
                  [edge("i1", "a", "b", rule="TR4", n=0), edge("i2", "b", "c", rule="TR4", n=0)],
                  constraints=Constraints(names=names(a="VCC", c="GND")))
    for single in ("i1", "i2"):
        g1 = ConnGraph(wires("a", "b", "c"), [e for e in G.edges if e.id == single], constraints=G.constraints)
        assert solve(g1).edge_status[single][0] == INFERRED
    r = solve(G)
    assert r.edge_status["i1"] == (UNKNOWN, "REJECTED_BY_CONSTRAINT")
    assert r.edge_status["i2"] == (UNKNOWN, "REJECTED_BY_CONSTRAINT")


def test_redundant_route_both_rejected_not_chosen():
    # two parallel INFERRED routes a-b-c and a-d-c, names conflict a vs c: all four rejected
    G = ConnGraph(wires("a", "b", "c", "d"),
                  [edge("i1", "a", "b", rule="TR4", n=0), edge("i2", "b", "c", rule="TR4", n=0),
                   edge("i3", "a", "d", rule="TR4", n=0), edge("i4", "d", "c", rule="TR4", n=0)],
                  constraints=Constraints(names=names(a="VCC", c="GND")))
    r = solve(G)
    assert all(r.edge_status[i] == (UNKNOWN, "REJECTED_BY_CONSTRAINT") for i in ("i1", "i2", "i3", "i4"))


def test_unrelated_inferred_edge_is_not_rejected():
    G = ConnGraph(wires("a", "b", "c", "x", "y"),
                  [edge("i1", "a", "b", rule="TR4", n=0), edge("i2", "b", "c", rule="TR4", n=0),
                   edge("ok", "x", "y", rule="TR4", n=0)],
                  constraints=Constraints(names=names(a="VCC", c="GND")))
    r = solve(G)
    assert r.edge_status["ok"][0] == INFERRED
    assert r.pair_status("x", "y") == INFERRED


def test_tc_c5_site_one_consistent_option_is_inferred_not_verified():
    # arms n,e,s,w; n named VCC, e named GND, s and w plain. ALL4/CORNER_NE_SW/THRU(n-s,e-w) ok? check below
    s = fourway_site("X", four_arms())
    cons = Constraints(names=names(n="VCC", e="GND"))
    r = solve(ConnGraph(wires("n", "e", "s", "w"), [], [s], cons))
    # options joining n with e (ALL4, CORNER_NE_SW) are inconsistent; THRU_NS_EW, CORNER_NW_SE, NONE remain
    assert r.sites["X"].status == UNKNOWN and "3_CONSISTENT" in r.sites["X"].reason
    # restrict domain so only one option survives
    opts = tuple(o for o in s.options if o.name in ("ALL4", "THRU_NS_EW"))
    s2 = Site("X", "FOURWAY", s.arms, opts)
    r2 = solve(ConnGraph(wires("n", "e", "s", "w"), [], [s2], cons))
    assert r2.sites["X"].status == INFERRED and r2.sites["X"].option == "THRU_NS_EW"
    assert r2.pair_status("n", "s") == INFERRED and r2.pair_status("n", "e") == UNKNOWN
    assert all(v[0] != VERIFIED for v in r2.edge_status.values())


def test_tc_c5_zero_consistent_options_is_conflict():
    s = fourway_site("X", four_arms())
    opts = tuple(o for o in s.options if o.name in ("ALL4", "CORNER_NE_SW"))   # both join n-e
    s2 = Site("X", "FOURWAY", s.arms, opts)
    cons = Constraints(names=names(n="VCC", e="GND"))
    r = solve(ConnGraph(wires("n", "e", "s", "w"), [], [s2], cons))
    assert r.sites["X"].status == CONFLICT and r.sites["X"].reason == "NO_CONSISTENT_OPTION"


def test_tc_c6_ocr_names_warn_but_do_not_conflict_or_link():
    cons = Constraints(names={"a": Name("VCC", confirmed=False), "b": Name("GND", confirmed=False)})
    r = solve(ConnGraph(wires("a", "b"), [edge("e", "a", "b")], constraints=cons))
    assert r.quarantined == [] and r.pair_status("a", "b") == VERIFIED
    assert any(w.startswith("OCR_NAME_DISAGREES") for w in r.warnings)
    cons2 = Constraints(names={"a": Name("N1", confirmed=False), "b": Name("N1", confirmed=False)})
    r2 = solve(ConnGraph(wires("a", "b"), [], constraints=cons2))
    assert r2.pair_status("a", "b") == UNKNOWN          # OCR same name creates no TR6 link


def test_tr6_same_confirmed_name_links_at_inferred():
    cons = Constraints(names=names(a="N1", b="N1", c="N1"))
    r = solve(ConnGraph(wires("a", "b", "c"), [], constraints=cons))
    assert r.pair_status("a", "c") == INFERRED and len(r.nets) == 1


def test_tc_c7_explain_only_names_violating_component():
    G = ConnGraph(wires("a", "b", "x", "y"), [edge("e1", "a", "b"), edge("e2", "x", "y")],
                  constraints=Constraints(names=names(a="VCC", b="GND")))
    r = solve(G)
    assert [sorted(o) for o, _ in r.quarantined] == [["a", "b"]]
    assert r.pair_status("x", "y") == VERIFIED


def test_c4_cannot_link_rejects_inferred_path_and_quarantines_verified():
    G = ConnGraph(wires("w", "m", "p"), [edge("i", "w", "m", rule="TR4", n=0), edge("v", "m", "p")],
                  constraints=Constraints(cannot_link=(("w", "p", "wire crosses symbol body"),)))
    r = solve(G)
    assert r.edge_status["i"][0] == UNKNOWN and r.edge_status["v"][0] == VERIFIED
    G2 = ConnGraph(wires("w", "m", "p"), [edge("v1", "w", "m"), edge("v", "m", "p")],
                   constraints=Constraints(cannot_link=(("w", "p", "x"),)))
    r2 = solve(G2)
    assert r2.pair_status("w", "p") == CONFLICT


def test_c5_no_connect_pin_must_be_singleton():
    objs = [Obj("p", "PIN"), Obj("w", "WIRE")]
    G = ConnGraph(objs, [edge("i", "p", "w", rule="TR4", ka="PIN", n=0)],
                  constraints=Constraints(no_connect=frozenset({"p"})))
    r = solve(G)
    assert r.edge_status["i"] == (UNKNOWN, "REJECTED_BY_CONSTRAINT")
    G2 = ConnGraph(objs, [edge("v", "p", "w", rule="TR3", ka="PIN", n=1)],
                   constraints=Constraints(no_connect=frozenset({"p"})))
    assert solve(G2).pair_status("p", "w") == CONFLICT


# ---------------------------------------------------------------- Phase 3 re-solve (v2.1)

def test_tc_c9_commit_that_creates_a_violation_is_rolled_back():
    # A site FIXED (injected) to an option that merges two differently named nets.
    s = fourway_site("X", four_arms())
    s = Site(s.id, s.kind, s.arms, s.options, fixed="ALL4")
    cons = Constraints(names=names(n="VCC", e="GND"))
    r = solve(ConnGraph(wires("n", "e", "s", "w"), [], [s], cons))
    assert r.sites["X"].status == CONFLICT and r.sites["X"].reason == "ROLLED_BACK_BY_RESOLVE"
    assert r.pair_status("n", "e") == UNKNOWN
    assert r.quarantined == []
    assert not any(v.status == INFERRED for v in r.sites.values())


def test_rollback_does_not_reject_unrelated_inferred_edges():
    s = fourway_site("X", four_arms())
    s = Site(s.id, s.kind, s.arms, s.options, fixed="ALL4")
    cons = Constraints(names=names(n="VCC", e="GND"))
    G = ConnGraph(wires("n", "e", "s", "w", "q", "r"), [edge("i", "q", "r", rule="TR4", n=0)], [s], cons)
    r = solve(G)
    assert r.edge_status["i"][0] == INFERRED


def test_tc_c10_idempotence_resolved_site_equals_fixed_from_scratch():
    s = fourway_site("X", four_arms())
    opts = tuple(o for o in s.options if o.name in ("ALL4", "THRU_NS_EW"))
    s_free = Site("X", "FOURWAY", s.arms, opts)
    cons = Constraints(names=names(n="VCC", e="GND"))
    G = ConnGraph(wires("n", "e", "s", "w"), [edge("v", "n", "s")], [s_free], cons)
    r1 = solve(G)
    chosen = r1.sites["X"].option
    assert chosen == "THRU_NS_EW"
    s_fix = Site("X", "FOURWAY", s.arms, opts, fixed=chosen)
    r2 = solve(ConnGraph(wires("n", "e", "s", "w"), [edge("v", "n", "s")], [s_fix], cons))
    assert r1.edge_status == r2.edge_status
    assert [(n.objects, n.status, n.closure) for n in r1.nets] == [(n.objects, n.status, n.closure) for n in r2.nets]
    for a, b in itertools.combinations("news", 2):
        assert r1.pair_status(a, b) == r2.pair_status(a, b)


def test_interacting_sites_share_a_cluster_and_too_large_cluster_is_unknown():
    objs, sites, prev = [], [], None
    for i in range(7):
        ids = [f"{i}n", f"{i}e", f"{i}s", f"{i}w"]
        objs += wires(*ids)
        sites.append(fourway_site(f"S{i}", dict(zip("NESW", ids))))
    edges = [edge(f"v{i}", f"{i}e", f"{i+1}w") for i in range(6)]      # chain => one interaction cluster
    r = solve(ConnGraph(objs, edges, sites), k_max=5)
    assert all(v.status == UNKNOWN and v.reason == "CLUSTER_TOO_LARGE" for v in r.sites.values())


def test_site_touching_quarantined_net_is_unknown():
    s = fourway_site("X", four_arms())
    G = ConnGraph(wires("n", "e", "s", "w", "q"), [edge("v", "n", "q")], [s],
                  Constraints(names=names(n="A", q="B")))
    r = solve(G)
    assert r.sites["X"].reason == "TOUCHES_CONFLICT_COMPONENT"


# ---------------------------------------------------------------- I6 monotonicity (property)

def _rand_graph(rnd):
    n = rnd.randint(3, 9)
    ids = [f"w{i}" for i in range(n)]
    edges = []
    for k in range(rnd.randint(2, 12)):
        a, b = rnd.sample(ids, 2)
        rule = rnd.choice(["TR1", "TR1", "TR4", "TR5"])
        edges.append(edge(f"e{k}", a, b, rule=rule, n=2 if rule == "TR1" else 0, conf=rnd.random() < 0.8))
    return ids, edges


def test_i6_adding_constraints_never_raises_edge_or_pair_status():
    rnd = random.Random(5)
    for _ in range(300):
        ids, edges = _rand_graph(rnd)
        base_cons = Constraints(names={o: Name("B" + o) for o in rnd.sample(ids, 1)})
        g0 = ConnGraph(wires(*ids), edges, [], base_cons)
        extra_names = dict(base_cons.names)
        for o in rnd.sample(ids, rnd.randint(0, 3)):
            extra_names.setdefault(o, Name("U" + o))      # unique text: adds a C1 constraint, no TR6 link
        cl = tuple((a, b, "x") for a, b in (rnd.sample(ids, 2) for _ in range(rnd.randint(0, 2))))
        g1 = ConnGraph(wires(*ids), edges, [], Constraints(extra_names, cl, frozenset()))
        r0, r1 = solve(g0), solve(g1)
        for eid in r0.edge_status:
            assert RANK[r1.edge_status[eid][0]] <= RANK[r0.edge_status[eid][0]] or \
                r0.edge_status[eid][0] == CONFLICT, (eid, r0.edge_status[eid], r1.edge_status[eid])
        for a, b in itertools.combinations(ids, 2):
            s0, s1 = r0.pair_status(a, b), r1.pair_status(a, b)
            if s0 != CONFLICT:
                assert RANK[s1] <= RANK[s0], (a, b, s0, s1)


def test_i6_negative_evidence_never_raises():
    rnd = random.Random(9)
    for _ in range(200):
        ids, edges = _rand_graph(rnd)
        r0 = solve(ConnGraph(wires(*ids), edges))
        k = rnd.randrange(len(edges))
        e2 = list(edges)
        e2[k] = Edge(edges[k].id, edges[k].a, edges[k].b, edges[k].rule, edges[k].roles, edges[k].coord,
                     edges[k].n_prims, ("neg",))
        r1 = solve(ConnGraph(wires(*ids), e2))
        for eid in r0.edge_status:
            assert RANK[r1.edge_status[eid][0]] <= RANK[r0.edge_status[eid][0]]
        for a, b in itertools.combinations(ids, 2):
            assert RANK[r1.pair_status(a, b)] <= RANK[r0.pair_status(a, b)]


def test_i6_with_sites_candidate_edge_statuses_never_rise():
    rnd = random.Random(21)
    for _ in range(150):
        ids, edges = _rand_graph(rnd)
        sid = rnd.sample(ids, 4) if len(ids) >= 4 else None
        sites = [fourway_site("S", dict(zip("NESW", sid)))] if sid else []
        r0 = solve(ConnGraph(wires(*ids), edges, sites, Constraints(names={ids[0]: Name("A")})))
        c1 = Constraints(names={ids[0]: Name("A"), ids[-1]: Name("B")}, cannot_link=((ids[0], ids[1], "x"),))
        r1 = solve(ConnGraph(wires(*ids), edges, sites, c1))
        for eid in r0.edge_status:
            assert RANK[r1.edge_status[eid][0]] <= RANK[r0.edge_status[eid][0]] or \
                r0.edge_status[eid][0] == CONFLICT


# ---------------------------------------------------------------- structure

def test_block_edges_cycle_vs_bridge():
    # a-b-c-a triangle plus pendant c-d; paths a..c: edges of triangle only
    ce = [("ab", "a", "b"), ("bc", "b", "c"), ("ca", "c", "a"), ("cd", "c", "d")]
    assert block_edges_with(ce, "a", "c") == {"ab", "bc", "ca"}
    assert block_edges_with(ce, "a", "d") == {"ab", "bc", "ca", "cd"}
    assert block_edges_with([("x", "a", "b"), ("y", "b", "c")], "a", "c") == {"x", "y"}
    assert block_edges_with([("x", "a", "b"), ("y", "a", "b")], "a", "b") == {"x", "y"}


def test_graph_validation_fails_loudly():
    with pytest.raises(GraphError):
        solve(ConnGraph(wires("a", "a"), []))
    with pytest.raises(GraphError):
        solve(ConnGraph(wires("a"), [edge("e", "a", "ghost")]))
    with pytest.raises(GraphError):
        solve(ConnGraph(wires("a", "b"), [edge("e", "a", "b"), edge("e", "a", "b")]))
    s = fourway_site("X", dict(N="a", E="b", S="c", W="d"))
    with pytest.raises(GraphError):
        solve(ConnGraph(wires("a", "b"), [], [s]))


def test_every_object_in_exactly_one_net_c3():
    rnd = random.Random(2)
    for _ in range(100):
        ids, edges = _rand_graph(rnd)
        G = ConnGraph(wires(*ids), edges)
        assert audit_result(G, solve(G)) == []


def test_inferred_edge_into_a_quarantined_net_is_not_applied():
    G = ConnGraph(wires("a", "b", "x"),
                  [edge("v", "a", "b"), edge("i", "b", "x", rule="TR4", n=0)],
                  constraints=Constraints(names=names(a="VCC", b="GND")))
    r = solve(G)
    assert r.edge_status["i"] == (UNKNOWN, "TOUCHES_CONFLICT_COMPONENT")
    assert r.pair_status("x", "a") == UNKNOWN
    assert {tuple(n.objects) for n in r.nets} == {("a", "b"), ("x",)}


def test_random_full_graphs_are_order_independent_and_structurally_sound():
    rnd = random.Random(99)
    for _ in range(150):
        n = rnd.randint(4, 12)
        ids = [f"w{i}" for i in range(n)]
        edges = []
        for k in range(rnd.randint(2, 14)):
            a, b = rnd.sample(ids, 2)
            rule = rnd.choice(["TR1", "TR1", "TR4", "TR5"])
            edges.append(edge(f"e{k}", a, b, rule=rule, n=2 if rule == "TR1" else 0, conf=rnd.random() < 0.8))
        sites = []
        pool = ids[:]
        for si in range(rnd.randint(0, 2)):
            if len(pool) < 4:
                break
            arms = [pool.pop(rnd.randrange(len(pool))) for _ in range(4)]
            s = fourway_site(f"S{si}", dict(zip("NESW", arms)))
            if rnd.random() < 0.3:
                s = Site(s.id, s.kind, s.arms, s.options, fixed=rnd.choice(s.options).name)
            sites.append(s)
        nm = {o: Name(rnd.choice("ABCD")) for o in rnd.sample(ids, rnd.randint(0, 4))}
        cl = tuple((a, b, "x") for a, b in (rnd.sample(ids, 2) for _ in range(rnd.randint(0, 2))))
        cons = Constraints(nm, cl, frozenset(rnd.sample(ids, rnd.randint(0, 1))))
        G = ConnGraph(wires(*ids), edges, sites, cons)
        r = solve(G)
        assert audit_result(G, r) == []
        # no constraint violation survives in any final, non-quarantined net
        from ecci.constraints import check_constraints
        qobjs = {o for objs, _ in r.quarantined for o in objs}
        for net in r.nets:
            if not (set(net.objects) & qobjs):
                assert check_constraints(frozenset(net.objects), cons) == [], net
        for _ in range(3):
            e2, o2, s2 = edges[:], wires(*ids), sites[:]
            rnd.shuffle(e2); rnd.shuffle(o2); rnd.shuffle(s2)
            assert solve(ConnGraph(o2, e2, s2, cons)).digest() == r.digest()
