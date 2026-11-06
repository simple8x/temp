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
    topo.segs = [Seg(0, 0, 10, 0, "line")]
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
    topo.segs = [Seg(0, 0, 10, 0, "line")]
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
