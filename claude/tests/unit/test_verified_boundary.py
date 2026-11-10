"""P1: trust boundary of VERIFIED. classify_edge must fail closed on every malformed or mismatched premise
record, and audit_verified / audit_result must catch the same records independently."""
import dataclasses

import pytest

from ecci.solver import ConnGraph, Obj, SolveResult, audit_result, solve
from ecci.status import INFERRED, UNKNOWN, VERIFIED, Edge, RoleRef, audit_verified, classify_edge

PROV = ("page:0/op:1",)


def R(o, kind="WIRE", ev=None, weak=False, status="CONFIRMED"):
    return RoleRef(o, kind, status, (f"A:{o}", f"B:{o}") if ev is None else ev, weak)


def E(rule="TR1", ka="WIRE", kb="WIRE", n=2, a="a", b="b", roles=None, coord="pt", prov=PROV, negative=()):
    return Edge("e", a, b, rule, roles or (R(a, ka), R(b, kb)), coord, n, negative, prov)


GOOD = [E(), E("TR2", "WIRE", "DOT", n=3), E("TR3", "PIN", "WIRE", n=1)]


@pytest.mark.parametrize("e", GOOD)
def test_complete_sound_records_are_verified_and_pass_the_audit(e):
    assert classify_edge(e) == (VERIFIED, "PREMISES_COMPLETE")
    assert audit_verified(e) == []


@pytest.mark.parametrize("rule,ka,kb,n", [("TR3", "PIN", "PIN", 1), ("TR3", "WIRE", "WIRE", 1),
                                          ("TR2", "WIRE", "WIRE", 3), ("TR2", "DOT", "DOT", 3)])
def test_sound_rule_needs_its_exact_role_kinds(rule, ka, kb, n):
    e = E(rule, ka, kb, n=n)
    assert classify_edge(e) == (UNKNOWN, "RULE_ROLE_KIND_MISMATCH")
    assert any("do not fit" in p for p in audit_verified(e))


def test_role_records_must_name_the_edge_endpoints():
    e = E(roles=(R("x"), R("b")))
    assert classify_edge(e) == (UNKNOWN, "ROLE_OBJ_MISMATCH")
    swapped = E(roles=(R("b"), R("a")))
    assert classify_edge(swapped) == (UNKNOWN, "ROLE_OBJ_MISMATCH")
    assert any("endpoints" in p for p in audit_verified(e))


@pytest.mark.parametrize("prov", [(), ("",), ("  ",), (None,), None])
def test_verified_needs_a_provenance_record(prov):
    e = E(prov=prov)
    assert classify_edge(e) == (INFERRED, "MISSING_PROVENANCE")
    assert "no provenance record" in audit_verified(e)


@pytest.mark.parametrize("ev", [("", "B:a"), ("A:a", " "), ("A:a", None), ["A:a", "B:a"], ("A:a", "A:a")])
def test_confirmed_role_needs_two_valid_distinct_evidence_ids(ev):
    e = E(roles=(R("a", ev=ev), R("b")))
    assert classify_edge(e)[0] == INFERRED
    assert any("valid independent evidence" in p for p in audit_verified(e))


@pytest.mark.parametrize("weak", [1, "yes", None, True])
def test_any_weak_flag_other_than_false_caps_at_inferred(weak):
    e = E(roles=(R("a", weak=weak), R("b")))
    assert classify_edge(e) == (INFERRED, "WEAK_SOURCE")
    assert any("RASTER/OCR" in p for p in audit_verified(e))


@pytest.mark.parametrize("n", [True, 2.0, "2", None])
def test_n_prims_must_be_a_real_int(n):
    assert classify_edge(E(n=n)) == (UNKNOWN, "BAD_N_PRIMS")


@pytest.mark.parametrize("neg", [None, ["x"], "x"])
def test_malformed_negative_evidence_fails_closed(neg):
    assert classify_edge(E(negative=neg)) == (UNKNOWN, "NEGATIVE_EVIDENCE")
    assert "negative evidence present" in audit_verified(E(negative=neg))


@pytest.mark.parametrize("coord", ["", "  ", None, 3])
def test_coordinate_record_must_be_a_non_blank_string(coord):
    assert classify_edge(E(coord=coord)) == (INFERRED, "MISSING_COORD_RECORD")
    assert "no coordinate record" in audit_verified(E(coord=coord))


def test_non_records_fail_closed():
    assert classify_edge(object())[0] == UNKNOWN
    assert classify_edge(E(roles=[R("a"), R("b")]))[0] == UNKNOWN
    assert classify_edge(dataclasses.replace(E(), roles=(R("a"), "b")))[0] == UNKNOWN
    assert classify_edge(dataclasses.replace(E(), rule=None))[0] == UNKNOWN
    assert classify_edge(dataclasses.replace(E(), a="", roles=(R(""), R("b"))))[0] == UNKNOWN
    assert audit_verified(object()) == ["not an Edge record"]


def test_audit_catches_n_prims_independently():
    assert any("primitives" in p for p in audit_verified(E(n=3)))
    assert any("primitives" in p for p in audit_verified(E("TR3", "PIN", "WIRE", n=2)))


def _solved():
    G = ConnGraph([Obj("a", "WIRE"), Obj("b", "WIRE")], [E()])
    return G, solve(G)


def test_audit_result_rejects_a_verified_status_classify_edge_would_not_give():
    G, res = _solved()
    assert res.edge_status["e"][0] == VERIFIED and audit_result(G, res) == []
    G2 = ConnGraph(G.objects, [E(prov=())])           # same id, record no longer complete
    assert any(p.startswith("I7 e:") for p in audit_result(G2, res))


def test_audit_result_rejects_a_verified_edge_that_is_not_in_the_graph():
    G, res = _solved()
    forged = dataclasses.replace(res, edge_status={**res.edge_status, "ghost": (VERIFIED, "PREMISES_COMPLETE")})
    assert isinstance(forged, SolveResult)
    assert "I7 ghost: VERIFIED edge is not in the graph" in audit_result(G, forged)
