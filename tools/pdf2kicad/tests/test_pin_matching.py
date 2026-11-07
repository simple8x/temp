"""Unit tests for conservative printed-pin identity matching."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf2kicad.netlist import _unambiguous_pin_assignment, _propagate_shared_terminal_names, _netlike, pin_tokens


def test_clear_mutual_matches_are_kept():
    assert _unambiguous_pin_assignment([
        [1.0, 8.0, 30.0],
        [9.0, 1.2, 25.0],
        [28.0, 24.0, 0.8],
    ]) == {0: 0, 1: 1, 2: 2}


def test_equal_distance_token_is_not_guessed():
    # One printed token is equally close to two physical terminals.
    assert _unambiguous_pin_assignment([
        [2.0],
        [2.0],
    ]) == {}


def test_terminal_near_two_tokens_is_not_guessed():
    assert _unambiguous_pin_assignment([
        [3.0, 3.8],
        [20.0, 20.0],
    ]) == {}


def test_token_claimed_by_better_terminal_is_not_stolen():
    assert _unambiguous_pin_assignment([
        [2.0, 10.0],
        [2.5, 11.0],
    ]) == {}


def test_out_of_range_and_empty_inputs():
    assert _unambiguous_pin_assignment([[13.0]]) == {}
    assert _unambiguous_pin_assignment([]) == {}
    assert _unambiguous_pin_assignment([[]]) == {}


def test_non_rectangular_matrix_rejected():
    import pytest
    with pytest.raises(ValueError):
        _unambiguous_pin_assignment([[1.0, 2.0], [3.0]])



def test_fragments_of_one_physical_pin_inherit_single_explicit_net():
    fragments = {("U1", 10.0, 20.0): {4, 9}}
    names = _propagate_shared_terminal_names(fragments, {4: "INPUT"})
    assert names == {4: "INPUT", 9: "INPUT"}


def test_conflicting_explicit_net_labels_are_not_silently_merged():
    fragments = {("J1", 10.0, 20.0): {4, 9}}
    names = _propagate_shared_terminal_names(fragments, {4: "SDA", 9: "SCL"})
    assert names == {4: "SDA", 9: "SCL"}


def test_unlabelled_fragments_remain_unresolved_without_a_label():
    fragments = {("U1", 10.0, 20.0): {4, 9}}
    names = _propagate_shared_terminal_names(fragments, {})
    assert names == {}



def test_fragments_of_one_physical_pin_inherit_single_explicit_net():
    fragments = {("U1", 10.0, 20.0): {4, 9}}
    names = _propagate_shared_terminal_names(fragments, {4: "INPUT"})
    assert names == {4: "INPUT", 9: "INPUT"}


def test_conflicting_explicit_net_labels_are_not_silently_merged():
    fragments = {("J1", 10.0, 20.0): {4, 9}}
    names = _propagate_shared_terminal_names(fragments, {4: "SDA", 9: "SCL"})
    assert names == {4: "SDA", 9: "SCL"}


def test_unlabelled_fragments_remain_unresolved_without_a_label():
    fragments = {("U1", 10.0, 20.0): {4, 9}}
    names = _propagate_shared_terminal_names(fragments, {})
    assert names == {}



def test_resistor_value_not_mistaken_for_net_label():
    assert not _netlike("2k2")
    assert not _netlike("4R7")
    assert _netlike("INPUT03")

def test_pin_number_inside_symbol_body_is_not_discarded():
    from types import SimpleNamespace

    terminals = [
        SimpleNamespace(x=10.0, y=10.0, dx=0.0, dy=0.0, comp=1),
        SimpleNamespace(x=30.0, y=30.0, dx=0.0, dy=0.0, comp=2),
        SimpleNamespace(x=50.0, y=50.0, dx=0.0, dy=0.0, comp=3),
    ]
    group = SimpleNamespace(
        x0=0.0, y0=0.0, x1=60.0, y1=60.0, terminals=terminals
    )
    topo = SimpleNamespace(groups=[group], used_words=set())
    token = SimpleNamespace(x0=9.5, y0=9.5, x1=10.5, y1=10.5, text="1")
    pins, claimed = pin_tokens(topo, [group], [token])
    assert pins == {id(terminals[0]): "1"}
    assert claimed == {id(token)}



def test_pin_matching_uses_terminal_count_not_wire_component_count():
    from types import SimpleNamespace

    terminals = [
        SimpleNamespace(x=10.0, y=10.0, dx=0.0, dy=0.0, comp=1),
        SimpleNamespace(x=30.0, y=10.0, dx=0.0, dy=0.0, comp=1),
        SimpleNamespace(x=50.0, y=10.0, dx=0.0, dy=0.0, comp=2),
    ]
    group = SimpleNamespace(
        x0=0.0, y0=0.0, x1=60.0, y1=20.0,
        terminals=terminals, no_connect_terminals=[],
    )
    topo = SimpleNamespace(groups=[group], used_words=set())
    tokens = [
        SimpleNamespace(x0=x - 0.5, y0=9.5, x1=x + 0.5, y1=10.5, text=str(i))
        for i, x in enumerate((10.0, 30.0, 50.0), start=1)
    ]
    pins, claimed = pin_tokens(topo, [group], tokens)
    assert pins == {id(terminals[i]): str(i + 1) for i in range(3)}
    assert claimed == {id(token) for token in tokens}



def test_two_terminal_pin_ids_are_recovered_without_order_guessing():
    from types import SimpleNamespace

    terminals = [
        SimpleNamespace(x=0.0, y=10.0, dx=0.0, dy=0.0, comp=1),
        SimpleNamespace(x=20.0, y=10.0, dx=0.0, dy=0.0, comp=2),
    ]
    group = SimpleNamespace(
        x0=0.0, y0=0.0, x1=20.0, y1=20.0,
        terminals=terminals, no_connect_terminals=[],
    )
    topo = SimpleNamespace(groups=[group], used_words=set())
    tokens = [
        SimpleNamespace(x0=-0.5, y0=9.5, x1=0.5, y1=10.5, text="1"),
        SimpleNamespace(x0=19.5, y0=9.5, x1=20.5, y1=10.5, text="2"),
    ]

    pins, claimed = pin_tokens(topo, [group], tokens)

    assert pins == {id(terminals[0]): "1", id(terminals[1]): "2"}
    assert claimed == {id(token) for token in tokens}
