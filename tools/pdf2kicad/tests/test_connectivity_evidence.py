import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf2kicad.connectivity_evidence import (\n    build_connectivity_evidence, build_terminal_reference_map, resolve_terminal_ref,\n)
from pdf2kicad.primitives import Seg
from pdf2kicad.topology import Terminal


def test_connectivity_ledger_traces_parser_membership_to_wire_primitives():
    attached = Terminal(10.0, 5.0, 0, kind="attach", group=2, dx=1.0)
    unresolved = Terminal(30.0, 5.0, 1, kind="open")
    topo = SimpleNamespace(
        W=[0, 1],
        comps=[[0], [1]],
        segs=[
            Seg(0.0, 5.0, 10.0, 5.0, "line"),
            Seg(20.0, 5.0, 30.0, 5.0, "line"),
        ],
        terminals=[attached, unresolved],
        groups=[SimpleNamespace(ref=""), SimpleNamespace(ref=""), SimpleNamespace(ref="R1")],
    )
    component = SimpleNamespace(ref="R1", terminals=[attached, unresolved])
    evidence = build_connectivity_evidence(
        topo, [component], {"N0": [("R1", attached)]}, {id(attached): "1"}
    )

    assert evidence["summary"]["parser_derived_memberships"] == 1
    assert evidence["summary"]["distinct_nets"] == 1
    assert evidence["summary"]["open_unresolved_terminals"] == 1
    assert evidence["summary"]["all_topology_terminals"] == 2
    assert evidence["summary"]["attached_terminals"] == 1
    assert evidence["summary"]["independently_verified_memberships"] == 0
    row = evidence["memberships"][0]
    assert row["status"] == "PARSER_DERIVED_NOT_INDEPENDENTLY_VERIFIED"
    assert row["pin_identity"] == "1"
    assert row["wire_component"] == 0
    assert row["wire_primitives"] == [{
        "primitive_index": 0,
        "kind": "line",
        "a": [0.0, 5.0],
        "b": [10.0, 5.0],
    }]
    assert evidence["unresolved_terminals"][0]["status"] == "OPEN_UNRESOLVED"


def test_conflicting_parser_pin_ownership_is_reported_not_hidden():
    a = Terminal(10.0, 5.0, 0, kind="attach", group=2)
    b = Terminal(20.0, 5.0, 1, kind="attach", group=2)
    topo = SimpleNamespace(
        W=[0, 1],
        comps=[[0], [1]],
        segs=[Seg(0, 5, 10, 5, "line"), Seg(20, 5, 30, 5, "line")],
        terminals=[a, b],
        groups=[SimpleNamespace(ref=""), SimpleNamespace(ref=""), SimpleNamespace(ref="U1")],
    )
    component = SimpleNamespace(ref="U1", terminals=[a, b])
    evidence = build_connectivity_evidence(
        topo, [component], {"NET_A": [("U1", a)], "NET_B": [("U1", b)]},
        {id(a): "A1", id(b): "A1"},
    )
    assert evidence["summary"]["pin_identity_conflicts"] == 1
    assert evidence["pin_conflicts"] == [{
        "ref": "U1", "pin_identity": "A1", "nets": ["NET_A", "NET_B"]
    }]



def test_geometry_inferred_pin_is_not_mislabeled_as_printed_text():
    terminal = Terminal(10.0, 5.0, 0, kind="attach", group=2)
    topo = SimpleNamespace(
        W=[0], comps=[[0]], segs=[Seg(0.0, 5.0, 10.0, 5.0, "line")],
        terminals=[terminal],
        groups=[SimpleNamespace(ref=""), SimpleNamespace(ref=""), SimpleNamespace(ref="D1")],
    )
    component = SimpleNamespace(ref="D1", terminals=[terminal])
    inferred = build_connectivity_evidence(
        topo, [component], {"NET": [("D1", terminal)]},
        {id(terminal): "A"},
    )
    assert inferred["memberships"][0]["pin_identity_kind"] == "geometry_inferred"
    assert inferred["terminal_inventory"][0]["pin_identity_kind"] == "geometry_inferred"

    printed = build_connectivity_evidence(
        topo, [component], {"NET": [("D1", terminal)]},
        {id(terminal): "A"}, explicit_pin_numbers={id(terminal): "A"},
    )
    assert printed["memberships"][0]["pin_identity_kind"] == "parser_pin_token"
    assert printed["terminal_inventory"][0]["pin_identity_kind"] == "parser_pin_token"


def test_terminal_ref_falls_back_to_unique_production_membership_ref():
    terminal = Terminal(629.52, 330.96, 0, kind="attach", group=99)
    group_ref = {1: "R1"}
    production_nets = {"N48": [("Y1", terminal)]}
    reference_by_terminal = build_terminal_reference_map(production_nets)

    assert resolve_terminal_ref(terminal, group_ref, reference_by_terminal) == "Y1"


def test_terminal_ref_does_not_guess_when_production_refs_conflict():
    terminal = Terminal(629.52, 330.96, 0, kind="attach", group=99)
    production_nets = {"N48": [("Y1", terminal)], "N49": [("U2", terminal)]}
    reference_by_terminal = build_terminal_reference_map(production_nets)

    assert resolve_terminal_ref(terminal, {}, reference_by_terminal) is None
