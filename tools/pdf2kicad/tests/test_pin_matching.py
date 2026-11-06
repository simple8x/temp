"""Unit tests for conservative printed-pin identity matching."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf2kicad.netlist import _unambiguous_pin_assignment, _propagate_shared_terminal_names, _propagate_shared_terminal_names


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
