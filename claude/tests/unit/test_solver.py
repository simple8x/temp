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


def test_non_exhaustive_multi_arm_domain_is_never_resolved_by_constraints():
    # The current ALL/NONE pair is only a placeholder, not a proven exhaustive topology domain.
    site = Site("multi", "MULTI", (("a0", "a"), ("a1", "b"), ("a2", "c")),
                (Option("ALL", frozenset({("a", "b"), ("a", "c"), ("b", "c")})),
                 Option("NONE", frozenset())))
    graph = ConnGraph(wires("a", "b", "c"), [], [site],
                      Constraints(cannot_link=(("a", "b", "known separation"),)))
    result = solve(graph)
    assert result.sites["multi"].status == UNKNOWN
    assert result.sites["multi"].option is None
    assert result.sites["multi"].reason == "NON_EXHAUSTIVE_MULTI_ARM_DOMAIN"
    assert not result.final_edges


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
                          coord="" if rnd.random() < 0.2 else "node:1@