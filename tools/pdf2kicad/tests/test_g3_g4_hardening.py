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
from pdf2kicad.netlist import PINTOK, _netlike, value_shape_matches


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
    term = Terminal(4, 3, 0, kind="open", anchor_kind="arrowhead")
    assert terminal_status(term) == "OPEN_UNRESOLVED"
    term.kind = "nc"
    assert terminal_status(term) == "NO_CONNECT_MARK"


def test_unmarked_open_pin_is_never_promoted_to_nc():
    term = Terminal(0, 0, 0, kind="open")
    assert terminal_status(term) == "OPEN_UNRESOLVED"
