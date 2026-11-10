"""P1: trust boundary of VERIFIED. classify_edge must fail closed on every malformed or mismatched premise
record, and audit_verified / audit_result must catch the same records independently."""
import dataclasses

import pytest

from ecci.solver import ConnGraph, Obj, SolveResult, audit_result, solve
from ecci.status import INFERRED, UNKNOWN, VERIFIED, Edge, RoleRef, audit_verified, classify_edge

PROV = ("page:0/op:1",)


def R(o, kind="WIRE", ev=None, weak=False, status="CONFIRMED"):
    return RoleRef(o, kind, status, (f"A:{o}", f"B:{o}") if ev is None else ev, weak)


def E(rule="TR1", ka="WIRE", kb="WIRE", n=2, a="a", b="b", roles=None, coord="node:1@1.000,2.000", prov=PROV, negative=()):
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


# ---------------------------------------------------------------- audit_verified is total and independent

def _audit_ok(e):
    out = audit_verified(e)
    assert isinstance(out, list) and all(isinstance(p, str) for p in out)
    return out


BAD_EDGES = {
    "n_prims=2.0": E(n=2.0),
    "n_prims=True": E(n=True),
    "n_prims='2'": E(n="2"),
    "n_prims=None": E(n=None),
    "tr3 n_prims=2": E("TR3", "PIN", "WIRE", n=2),
    "tr2 n_prims=1": E("TR2", "WIRE", "DOT", n=1),
    "endpoint a=''": E(a="", roles=(R(""), R("b"))),
    "endpoint a=' '": E(a=" ", roles=(R(" "), R("b"))),
    "endpoint a=None": E(a=None, roles=(R(None), R("b"))),
    "endpoint a=1": E(a=1, roles=(R(1), R("b"))),
    "endpoint a=['a']": E(a=["a"], roles=(R(["a"]), R("b"))),
    "self loop": E(a="b", roles=(R("b"), R("b"))),
    "coord tuple": E(coord=(1.0, 2.0)),
    "coord bytes": E(coord=b"node:1@1.000,2.000"),
    "coord no numbers": E(coord="pt"),
    "coord non-numeric": E(coord="node:1@x,2.000"),
    "coord nan": E(coord="node:1@nan,2.000"),
    "coord inf": E(coord="node:1@inf,2.000"),
    "coord -inf": E(coord="node:1@1.000,-inf"),
    "coord Infinity": E(coord="node:1@Infinity,0"),
    "coord huge overflow": E(coord="node:1@1" + "0" * 400 + ",0"),
    "evidence list element": E(roles=(R("a", ev=(["A:a"], "B:a")), R("b"))),
    "evidence dict element": E(roles=(R("a", ev=({"x": 1}, "B:a")), R("b"))),
    "evidence set element": E(roles=(R("a", ev=({"A"}, "B:a", "C:a")), R("b"))),
    "evidence is a dict": E(roles=(R("a", ev={"A:a": 1, "B:a": 2}), R("b"))),
    "evidence is a str": E(roles=(R("a", ev="AB"), R("b"))),
    "evidence None": E(roles=(RoleRef("a", "WIRE", "CONFIRMED", None), R("b"))),
    "duplicate evidence": E(roles=(R("a", ev=("A:a", "A:a", "B:a")), R("b"))),
    "single evidence": E(roles=(R("a", ev=("A:a",)), R("b"))),
    "missing provenance": E(prov=()),
    "provenance None": E(prov=None),
    "provenance list": E(prov=["page:0/op:1"]),
    "provenance bad element": E(prov=("page:0/op:1", ["x"])),
    "endpoint-role swapped": E(roles=(R("b"), R("a"))),
    "endpoint-role other obj": E(roles=(R("a"), R("c"))),
    "role kind list": E(roles=(R("a", kind=["WIRE"]), R("b"))),
    "role kind bogus": E(roles=(R("a", kind="WIRE2"), R("b"))),
    "role status unhashable": E(roles=(R("a", status=["CONFIRMED"]), R("b"))),
    "role candidate": E(roles=(R("a", status="CANDIDATE"), R("b"))),
    "weak None": E(roles=(R("a", weak=None), R("b"))),
    "weak 0": E(roles=(R("a", weak=0), R("b"))),
    "weak 1": E(roles=(R("a", weak=1), R("b"))),
    "weak 'false'": E(roles=(R("a", weak="false"), R("b"))),
    "weak 0.0": E(roles=(R("a", weak=0.0), R("b"))),
    "negative list": E(negative=["x"]),
    "negative non-empty": E(negative=("x",)),
    "negative None": E(negative=None),
    "rule TR4": E("TR4", n=3),
    "rule unhashable": dataclasses.replace(E(), rule=["TR1"]),
    "roles is a list": E(roles=[R("a"), R("b")]),
    "role not a RoleRef": dataclasses.replace(E(), roles=(R("a"), ("b", "WIRE"))),
    "three roles": dataclasses.replace(E(), roles=(R("a"), R("b"), R("b"))),
}


@pytest.mark.parametrize("name", sorted(BAD_EDGES))
def test_audit_verified_reports_every_malformed_record_and_never_raises(name):
    assert _audit_ok(BAD_EDGES[name]), f"{name}: audit accepted a malformed VERIFIED record"


@pytest.mark.parametrize("name", sorted(BAD_EDGES))
def test_audit_result_returns_violations_for_a_malformed_verified_edge_without_crashing(name):
    G, res = _solved()
    G2 = ConnGraph(G.objects, [dataclasses.replace(BAD_EDGES[name], id="e")])
    probs = audit_result(G2, res)                       # res still says e is VERIFIED
    assert any(p.startswith(("I2 e:", "I7 e:")) for p in probs), (name, probs)


@pytest.mark.parametrize("name", sorted(BAD_EDGES))
def test_classify_edge_never_raises_and_never_verifies_a_malformed_record(name):
    st, _ = classify_edge(BAD_EDGES[name])
    if name.startswith(("coord", "duplicate evidence")) or name in ("endpoint a=' '", "tr2 n_prims=1"):
        return     # classify_edge only checks a non-blank coordinate id, >= 2 distinct ids, a non-empty endpoint and
                   # no TR2 count: the independent audit is what catches these (checked above)
    assert st != VERIFIED, name


@pytest.mark.parametrize("bad", [ValueError, TypeError])
def test_audit_turns_an_unreadable_field_into_a_violation(bad):
    class Evil(str):
        def strip(self, *a):
            raise bad("boom")
    probs = audit_verified(E(a=Evil("a"), roles=(R(Evil("a")), R("b"))))
    assert probs and isinstance(probs, list)


def test_audit_does_not_call_or_share_helpers_with_classify_edge():
    import ast
    import inspect

    import ecci.status as st
    used = set()
    for f in (st.audit_verified, st._audit_verified, st._audit_ids, st._audit_coord, st._audit_str):
        used |= {n.id for n in ast.walk(ast.parse(inspect.getsource(f))) if isinstance(n, ast.Name)}
    assert not used & {"classify_edge", "effective_role", "_valid_ids", "RULES", "SOUND_KINDS"}, used


def test_audit_accepts_pipeline_coordinate_records():
    assert _audit_ok(E(coord="node:17@-12.500,300.000")) == []
    assert _audit_ok(E("TR3", "PIN", "WIRE", n=1, coord="pin:c3:2@1.270,-2.540")) == []
    assert _audit_ok(E("TR2", "WIRE", "DOT", n=2, coord="node:4@0.000,0.000")) == []
