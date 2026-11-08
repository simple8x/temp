import pytest

from pdf2kicad.vector_text_audit import (
    PIN_TOKEN, REF_TOKEN, box_distance, is_pin_token_candidate,
)


@pytest.mark.parametrize("token", ["1", "2", "12", "A", "K", "A+", "B-"])
def test_pin_token_regex_accepts_conservative_pin_forms(token):
    assert PIN_TOKEN.fullmatch(token)
    assert is_pin_token_candidate(token)


@pytest.mark.parametrize("token", [
    "", "123", "1234", "ABC", "R-1", r"\\d", "A++",
    "A1", "AB12", "B12", "C5", "U3", "SS34", "360",
])
def test_pin_token_regex_rejects_ambiguous_or_non_pin_forms(token):
    assert not PIN_TOKEN.fullmatch(token)
    assert not is_pin_token_candidate(token)


@pytest.mark.parametrize("token", ["R1", "C12", "RN3", "SW2", "U10A", "J1"])
def test_reference_tokens_are_not_pin_candidates(token):
    assert REF_TOKEN.fullmatch(token)
    assert not is_pin_token_candidate(token)


def test_box_distance_is_zero_inside_bbox():
    assert box_distance((5, 5), [0, 0, 10, 10]) == 0


def test_box_distance_uses_nearest_edge_not_bbox_center():
    assert box_distance((13, 4), [0, 0, 10, 10]) == 3


def test_box_distance_handles_diagonal_gap():
    assert round(box_distance((13, 14), [0, 0, 10, 10]), 6) == 5
