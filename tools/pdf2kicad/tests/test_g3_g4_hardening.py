import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf2kicad.primitives import Seg
from pdf2kicad.topology import (
    Terminal,
    exclude_rectangle_internals,
    find_symbol_rectangles,
    is_arrowhead_geometry,
    terminal_status,
    find_no_connect_crosses,
    suppress_nc_stub_body_ends,
)
from pdf2kicad.netlist import PINTOK, _is_pin_text, _netlike, value_shape_matches


def test_closed_rectangle_detects_body_and_excludes_only_internal_wire():
    rect = [
        Seg(0, 0, 30, 0, "rect"),
        Seg(30, 0, 30, 20, "rect"),
        Seg(30, 20, 0, 20, "rect"),
        Seg(0, 20, 0, 0, "rect"),
    ]
    internal = Seg(5, 10, 25, 10, "line")
    crossing = Seg(-5, 10, 5, 10, "line")
    boxes = find_symbol_rectangles(rect + [internal, crossing])
    assert boxes == [(0.0, 0.0, 30.0, 20.0)]
    assert exclude_rectangle_internals(rect + [internal, crossing], boxes) == 1
    assert internal.kind == "inner"
    assert crossing.kind == "line"


def test_pin_caption_values_are_not_net_names():
    assert not _netlike("USB-C")
    assert not _netlike("5k1")
    assert not _netlike("2k2")


def test_pin_captions_inside_body_or_on_pin_stub_are_not_net_labels():
    from types import SimpleNamespace

    term = Terminal(10.0, 0.0, 0, kind="attach")
    group = SimpleNamespace(x0=0.0, y0=-2.0, x1=2.0, y1=2.0, terminals=[term])
    topo = SimpleNamespace(comps=[[0]], W=[0], segs=[Seg(0, 0, 10, 0, "line")])
    inside = SimpleNamespace(x0=0.5, y0=0.0, x1=1.5, y1=1.0, text="VIN")
    near_stub = SimpleNamespace(x0=8.5, y0=0.5, x1=9.5, y1=1.0, text="A2")
    far_label = SimpleNamespace(x0=20.0, y0=20.0, x1=24.0, y1=21.0, text="SENSOR")
    assert _is_pin_text(inside, [group], topo)
    assert _is_pin_text(near_stub, [group], topo)
    assert not _is_pin_text(far_label, [group], topo)


def test_two_digit_and_lettered_pin_tokens_are_supported():
    assert PINTOK.fullmatch("A2")
    assert PINTOK.fullmatch("B12")
    assert PINTOK.fullmatch("12")


def test_connector_and_resistor_array_values_match_their_ref_families():
    assert value_shape_matches("J2", "USB-C")
    assert value_shape_matches("R12", "5k1")
    assert value_shape_matches("RN1", "2k2")
    assert value_shape_matches("RN2", "2k2")


def test_arrowhead_is_a_pin_marker_not_an_nc_mark():
    arrow = [Seg(0, 0, 4, 3, "line"), Seg(0, 6, 4, 3, "line")]
    assert is_arrowhead_geometry(arrow)
    diode_triangle = [
        Seg(0, 0, 4, 3, "line"),
        Seg(0, 6, 4, 3, "line"),
        Seg(0, 0, 0, 6, "line"),
    ]
    assert not is_arrowhead_geometry(diode_triangle)
    term = Terminal(4, 3, 0, kind="open", anchor_kind="arrowhead")
    assert terminal_status(term) == "OPEN_UNRESOLVED"
    term.kind = "nc"
    assert terminal_status(term) == "NO_CONNECT_MARK"


def test_unmarked_open_pin_is_never_promoted_to_nc():
    term = Terminal(0, 0, 0, kind="open")
    assert terminal_status(term) == "OPEN_UNRESOLVED"


def test_usb_c_connector_all_24_lettered_pin_tokens_assign_uniquely():
    from types import SimpleNamespace
    from pdf2kicad.topology import Terminal
    from pdf2kicad.netlist import pin_tokens

    tokens = [f"{side}{number}" for side in ("A", "B") for number in range(1, 13)]
    terminals = [
        Terminal(30.0 + 20.0 * i, 100.0, i, kind="attach", dx=1.0, dy=0.0)
        for i in range(len(tokens))
    ]
    group = SimpleNamespace(x0=0.0, y0=0.0, x1=600.0, y1=200.0, terminals=terminals)
    words = [
        SimpleNamespace(x0=term.x + 1.0, x1=term.x + 3.0,
                        y0=term.y - 1.0, y1=term.y + 1.0, text=token)
        for term, token in zip(terminals, tokens)
    ]
    topo = SimpleNamespace(used_words=set())

    assigned, claimed = pin_tokens(topo, [group], words)

    assert len(assigned) == 24
    assert {assigned[id(term)] for term in terminals} == set(tokens)
    assert len(claimed) == 24



def test_explicit_nc_cross_overrides_nearby_symbol_body_attachment():
    from pdf2kicad.topology import Topology, Grid

    topo = Topology.__new__(Topology)
    topo.segs = [Seg(0, 0, 10, 0, "line"), Seg(0, 0, 0, 5, "line")]
    topo.W = [0]
    topo.comps = [[0]]
    topo.wire_comps = [0]
    topo.body_segs = [Seg(0, 0, 0, 5, "line")]
    topo.marks = [(0, 0)]
    topo.bgrid = Grid(8.0)
    topo._dangling = lambda cid: [(0, 0)]
    topo._terminals()

    assert len(topo.terminals) == 1
    term = topo.terminals[0]
    assert term.kind == "nc"
    assert term.anchor_kind == "no_connect_cross"
    assert term.nc_mark_distance == 0.0
    assert terminal_status(term) == "NO_CONNECT_MARK"


def test_distant_x_glyph_does_not_override_body_attachment():
    from pdf2kicad.topology import Topology, Grid

    topo = Topology.__new__(Topology)
    topo.segs = [Seg(0, 0, 10, 0, "line"), Seg(0, 0, 0, 5, "line")]
    topo.W = [0]
    topo.comps = [[0]]
    topo.wire_comps = [0]
    topo.body_segs = [Seg(0, 0, 0, 5, "line")]
    topo.marks = [(5, 5)]
    topo.bgrid = Grid(8.0)
    topo._dangling = lambda cid: [(0, 0)]
    topo._terminals()

    assert topo.terminals[0].kind == "attach"
    assert topo.terminals[0].anchor_kind != "no_connect_cross"



def test_vector_cross_detector_accepts_centered_orthogonal_x():
    cross = [
        Seg(-2, -2, 2, 2, "line"),
        Seg(-2, 2, 2, -2, "line"),
    ]
    centres = find_no_connect_crosses(cross)
    assert len(centres) == 1
    assert centres[0] == (0.0, 0.0)


def test_vector_cross_detector_rejects_non_crossing_or_non_orthogonal_marks():
    offset = [
        Seg(-2, -2, 2, 2, "line"),
        Seg(1, 2, 5, -2, "line"),
    ]
    shallow = [
        Seg(-2, -2, 2, 2, "line"),
        Seg(-2, -1, 2, 3, "line"),
    ]
    assert find_no_connect_crosses(offset) == []
    assert find_no_connect_crosses(shallow) == []



def test_no_connect_terminal_is_assigned_to_symbol_but_not_net_membership():
    from pdf2kicad.topology import Topology, Terminal

    topo = Topology.__new__(Topology)
    topo.body_segs = [Seg(0, 0, 20, 0, "line")]
    terminal = Terminal(10, 0, 0, kind="nc", body=0, anchor_kind="no_connect_cross")
    topo.terminals = [terminal]
    topo._groups()

    assert terminal.group >= 0
    group = topo.groups[terminal.group]
    assert group.no_connect_terminals == [terminal]
    assert group.terminals == []



def test_pin_token_recovery_includes_nc_pins_without_counting_them_as_connected():
    from types import SimpleNamespace
    from pdf2kicad.topology import Terminal
    from pdf2kicad.netlist import pin_tokens

    attached = [
        Terminal(20.0 + 20.0 * i, 100.0, i, kind="attach", dx=1.0)
        for i in range(3)
    ]
    no_connect = Terminal(80.0, 100.0, 3, kind="nc", anchor_kind="no_connect_cross")
    group = SimpleNamespace(
        x0=0.0, y0=0.0, x1=200.0, y1=200.0,
        terminals=attached, no_connect_terminals=[no_connect],
    )
    all_terms = attached + [no_connect]
    words = [
        SimpleNamespace(x0=term.x + 1.0, x1=term.x + 3.0,
                        y0=term.y - 1.0, y1=term.y + 1.0, text=token)
        for term, token in zip(all_terms, ["1", "2", "3", "4"])
    ]
    topo = SimpleNamespace(used_words=set())

    assigned, claimed = pin_tokens(topo, [group], words)

    assert assigned[id(no_connect)] == "4"
    assert len(assigned) == 4
    assert len(group.terminals) == 3
    assert group.no_connect_terminals == [no_connect]



def test_nc_cross_at_pin_stub_end_traces_forward_to_symbol_body():
    from pdf2kicad.topology import Topology

    topo = Topology.__new__(Topology)
    topo.segs = [Seg(0, 0, 10, 0, "line"), Seg(8, -2, 8, 2, "line")]
    topo.W = [0]
    topo.comps = [[0]]
    topo.wire_comps = [0]
    topo.marks = [(0, 0)]
    topo._dangling = lambda cid: [(0, 0)]
    topo._terminals()

    term = topo.terminals[0]
    assert term.kind == "nc"
    assert term.body == 0
    topo._groups()
    assert term.group >= 0
    assert topo.groups[term.group].no_connect_terminals == [term]
    assert topo.groups[term.group].terminals == []




def test_pin_token_assignment_resolves_shared_nearest_caption_globally():
    from types import SimpleNamespace
    from pdf2kicad.topology import Terminal
    from pdf2kicad.netlist import pin_tokens

    # Both first terminals prefer caption "1" under independent nearest-neighbour
    # matching. A global one-to-one assignment correctly gives the second terminal
    # "1" and the first terminal "2", without reusing either caption.
    terminals = [
        Terminal(0.0, 100.0, 0, kind="attach"),
        Terminal(2.0, 100.0, 1, kind="attach"),
        Terminal(20.0, 100.0, 2, kind="attach"),
    ]
    group = SimpleNamespace(
        x0=-10.0, y0=80.0, x1=40.0, y1=120.0,
        terminals=terminals, no_connect_terminals=[],
    )
    words = [
        SimpleNamespace(x0=-4.0, x1=-2.0, y0=99.0, y1=101.0, text="2"),
        SimpleNamespace(x0=0.0, x1=2.0, y0=99.0, y1=101.0, text="1"),
        SimpleNamespace(x0=19.0, x1=21.0, y0=99.0, y1=101.0, text="3"),
    ]
    topo = SimpleNamespace(used_words=set())

    assigned, claimed = pin_tokens(topo, [group], words)

    assert assigned[id(terminals[0])] == "2"
    assert assigned[id(terminals[1])] == "1"
    assert assigned[id(terminals[2])] == "3"
    assert len(claimed) == 3

def test_nc_component_association_requires_direction_and_unique_candidate():
    from types import SimpleNamespace
    from pdf2kicad.topology import Terminal
    from pdf2kicad.netlist import assign_no_connect_terminals

    near = SimpleNamespace(ref="J2", x0=8.0, y0=-2.0, x1=12.0, y1=2.0, no_connect_terminals=[])
    wrong_direction = SimpleNamespace(ref="R1", x0=-12.0, y0=-2.0, x1=-8.0, y1=2.0, no_connect_terminals=[])
    term = Terminal(0.0, 0.0, 0, kind="nc", dx=1.0, dy=0.0)
    topo = SimpleNamespace(terminals=[term], groups=[near, wrong_direction])
    assigned = assign_no_connect_terminals(topo, [near, wrong_direction])
    assert len(assigned) == 1
    assert assigned[0]["ref"] == "J2"
    assert term.group == 0
    assert near.no_connect_terminals == [term]

    a = SimpleNamespace(ref="U1", x0=8.0, y0=-2.0, x1=12.0, y1=2.0, no_connect_terminals=[])
    b = SimpleNamespace(ref="U2", x0=8.0, y0=-2.0, x1=12.0, y1=2.0, no_connect_terminals=[])
    ambiguous = Terminal(0.0, 0.0, 1, kind="nc", dx=1.0, dy=0.0)
    topo2 = SimpleNamespace(terminals=[ambiguous], groups=[a, b])
    assert assign_no_connect_terminals(topo2, [a, b]) == []
    assert ambiguous.group == -1



def test_nc_reassignment_escapes_unreferenced_geometry_group():
    from types import SimpleNamespace
    from pdf2kicad.topology import Terminal
    from pdf2kicad.netlist import assign_no_connect_terminals

    term = Terminal(0.0, 0.0, 0, kind="nc", dx=1.0, dy=0.0, group=0)
    orphan = SimpleNamespace(ref="", no_connect_terminals=[term])
    component = SimpleNamespace(ref="J2", x0=8.0, y0=-2.0, x1=12.0, y1=2.0, no_connect_terminals=[])
    topo = SimpleNamespace(terminals=[term], groups=[orphan, component])

    assigned = assign_no_connect_terminals(topo, [component])

    assert len(assigned) == 1
    assert assigned[0]["ref"] == "J2"
    assert term.group == 1
    assert orphan.no_connect_terminals == []
    assert component.no_connect_terminals == [term]



def test_nc_stub_suppresses_duplicate_body_end_without_creating_a_net_pin():
    marked = Terminal(0, 0, 7, kind="nc")
    body_end = Terminal(10, 0, 7, kind="attach", body=3)
    conflicts = suppress_nc_stub_body_ends([marked, body_end])

    assert conflicts == []
    assert marked.body == 3
    assert body_end.kind == "nc_stub_body_end"
    assert terminal_status(body_end) == "SUPPRESSED_NC_STUB_BODY_END"


def test_nc_stub_with_multiple_attached_branches_fails_closed():
    marked = Terminal(0, 0, 7, kind="nc")
    a = Terminal(10, 0, 7, kind="attach", body=3)
    b = Terminal(10, 5, 7, kind="attach", body=4)
    conflicts = suppress_nc_stub_body_ends([marked, a, b])

    assert len(conflicts) == 1
    assert all(t.kind == "nc_topology_conflict" for t in [marked, a, b])
    assert all(terminal_status(t) == "NC_STUB_TOPOLOGY_CONFLICT" for t in [marked, a, b])
