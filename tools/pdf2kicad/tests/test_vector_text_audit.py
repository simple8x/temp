from pdf2kicad.vector_text_audit import box_distance


def test_box_distance_is_zero_inside_bbox():
    assert box_distance((5, 5), [0, 0, 10, 10]) == 0


def test_box_distance_uses_nearest_edge_not_bbox_center():
    assert box_distance((13, 4), [0, 0, 10, 10]) == 3


def test_box_distance_handles_diagonal_gap():
    assert round(box_distance((13, 14), [0, 0, 10, 10]), 6) == 5
