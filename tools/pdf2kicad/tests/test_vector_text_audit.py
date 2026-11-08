import pytest

from pdf2kicad.vector_text_audit import PIN_TOKEN, REF_TOKEN, box_distance


@pytest.mark.parametrize("token", ["1", "12", "123", "A", "A1", "AB12", "A+", "B-"])
def test_pin_token_regex_accepts_expected_forms(token):
    assert PIN_TOKEN.fullmatch(token)


@pytest.mark.parametrize("token", ["", "1234", "ABC", "R-1", r"\d", "A++"])
def test_pin_token_regex_rejects_non_tokens(token):
    assert not PIN_TOKEN.fullmatch(token)


@pytest.mark.parametrize("token", ["R1", "C12", "RN3", "SW2", "U10A"])
def test_reference_token_regex_accepts_expected_forms(token):
    assert REF_TOKEN.fullmatch(token)


@pytest.mark.parametrize("token", ["R", "ABC", "R-1", r"\d", "RN", "SW"])
def test_reference_token_regex_rejects_non_references(token):
    assert not REF_TOKEN.fullmatch(token)


def test_box_distance_is_zero_inside_bbox():
    assert box_distance((5, 5), [0, 0, 10, 10]) == 0


def test_box_distance_uses_nearest_edge_not_bbox_center():
    assert box_distance((13, 4), [0, 0, 10, 10]) == 3


def test_box_distance_handles_diagonal_gap():
    assert round(box_distance((13, 14), [0, 0, 10, 10]), 6) == 5
