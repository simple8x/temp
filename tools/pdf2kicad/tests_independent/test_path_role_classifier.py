import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from path_role_classifier import classify_path_groups


def p(i, path, a, b):
    return {"id": i, "path_index": path, "a": a, "b": b}


def test_closed_rectangle_is_candidate_not_verified_symbol():
    result = classify_path_groups([
        p(0, 1, [0, 0], [10, 0]),
        p(1, 1, [10, 0], [10, 5]),
        p(2, 1, [10, 5], [0, 5]),
        p(3, 1, [0, 5], [0, 0]),
    ])
    row = result["paths"][0]
    assert row["rectangular_path_candidate"] is True
    assert row["role_candidate"] == "CLOSED_RECTANGULAR_PATH_CANDIDATE"
    assert row["authority"] == "CANDIDATE_ONLY"
    assert result["wire_classification"] == "NOT_ATTEMPTED"


def test_touching_single_strokes_are_not_promoted_to_wire():
    result = classify_path_groups([
        p(0, 1, [0, 0], [10, 0]),
        p(1, 2, [10, 0], [10, 10]),
    ])
    assert all(x["role_candidate"] == "SINGLE_STROKE_AMBIGUOUS" for x in result["paths"])
    assert "WIRE_CANDIDATE" not in result["primitive_role_counts"]


def test_open_multi_edge_path_remains_ambiguous():
    result = classify_path_groups([
        p(0, 1, [0, 0], [10, 0]),
        p(1, 1, [10, 0], [10, 5]),
    ])
    assert result["paths"][0]["role_candidate"] == "MULTI_EDGE_OPEN_PATH_AMBIGUOUS"
