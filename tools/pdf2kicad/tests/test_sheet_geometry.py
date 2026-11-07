from types import SimpleNamespace

from pdf2kicad.primitives import Seg
from pdf2kicad.sheet_geometry import exclude_sheet_metadata


def test_page_frame_requires_orthogonal_page_edge_geometry():
    segs = [
        Seg(8, 8, 592, 8, "line"),
        Seg(592, 8, 592, 792, "line"),
        Seg(592, 792, 8, 792, "line"),
        Seg(8, 792, 8, 8, "line"),
        Seg(30, 20, 120, 20, "line"),  # real circuit wire near top, not at page edge
    ]
    report = exclude_sheet_metadata(segs, 600, 800)
    assert report["sheet_frame_segments_excluded"] == 4
    assert segs[0].kind == "sheet_frame"
    assert segs[4].kind == "line"


def test_single_long_edge_wire_is_not_classified_as_page_frame():
    segs = [Seg(20, 8, 580, 8, "line")]
    report = exclude_sheet_metadata(segs, 600, 800)
    assert report["sheet_frame_segments_excluded"] == 0
    assert segs[0].kind == "line"


def test_title_block_requires_closed_enclosure_and_text_evidence():
    segs = [
        Seg(380, 650, 590, 650, "line"),
        Seg(590, 650, 590, 790, "line"),
        Seg(590, 790, 380, 790, "line"),
        Seg(380, 790, 380, 650, "line"),
        Seg(390, 720, 580, 720, "line"),  # internal table rule
        Seg(340, 100, 580, 100, "line"),  # unrelated circuit wire
    ]
    words = [
        (400, 670, 440, 680, "PROJECT"),
        (450, 750, 490, 760, "REV"),
    ]
    report = exclude_sheet_metadata(segs, 600, 800, words)
    assert report["title_block_segments_excluded"] == 5
    assert all(segs[i].kind == "title_block" for i in range(5))
    assert segs[5].kind == "line"


def test_open_box_or_box_without_text_is_not_excluded():
    segs = [
        Seg(380, 650, 590, 650, "line"),
        Seg(590, 650, 590, 790, "line"),
        Seg(590, 790, 380, 790, "line"),
    ]
    report = exclude_sheet_metadata(segs, 600, 800, ())
    assert report["title_block_segments_excluded"] == 0
    assert all(s.kind == "line" for s in segs)

def test_long_internal_wire_is_not_removed_by_length_heuristic():
    segs = [Seg(35, 300, 565, 300, "line")]
    report = exclude_sheet_metadata(segs, 600, 800)
    assert report["sheet_frame_segments_excluded"] == 0
    assert report["title_block_segments_excluded"] == 0
    assert segs[0].kind == "line"

def test_fragmented_page_frame_is_detected_by_aggregated_edge_coverage():
    segs = []
    for x in range(8, 592, 58):
        segs.append(Seg(x, 8, min(x + 50, 592), 8, "line"))
        segs.append(Seg(x, 792, min(x + 50, 592), 792, "line"))
    for y in range(8, 792, 78):
        segs.append(Seg(8, y, 8, min(y + 70, 792), "line"))
        segs.append(Seg(592, y, 592, min(y + 70, 792), "line"))
    report = exclude_sheet_metadata(segs, 600, 800)
    assert report["sheet_frame_segments_excluded"] == len(segs)
    assert all(s.kind == "sheet_frame" for s in segs)

def test_top_edge_title_block_requires_closed_box_and_text():
    segs = [
        Seg(100, 8, 260, 8, "line"),
        Seg(260, 8, 260, 50, "line"),
        Seg(260, 50, 100, 50, "line"),
        Seg(100, 50, 100, 8, "line"),
        Seg(110, 28, 250, 28, "line"),
    ]
    words = [
        (112, 12, 160, 20, "DRAWING"),
        (170, 34, 220, 42, "SHEET"),
    ]
    report = exclude_sheet_metadata(segs, 842, 595, words)
    assert report["title_block_segments_excluded"] == 5
    assert all(s.kind == "title_block" for s in segs)

def test_large_sheet_enclosure_with_many_words_is_not_a_title_block():
    segs = [
        Seg(8, 8, 834, 8, "line"),
        Seg(834, 8, 834, 587, "line"),
        Seg(834, 587, 8, 587, "line"),
        Seg(8, 587, 8, 8, "line"),
    ]
    words = [
        (40, 40, 90, 50, "SHEET"),
        (100, 40, 150, 50, "SCHEMATIC"),
        (40, 100, 90, 110, "U1"),
    ]
    report = exclude_sheet_metadata(segs, 842, 595, words)
    assert report["title_block_segments_excluded"] == 0
    assert report["sheet_frame_segments_excluded"] == 4
    assert all(s.kind == "sheet_frame" for s in segs)


def test_rounded_confidentiality_badge_at_page_edge_is_metadata_not_electrical():
    segs = [
        Seg(533.40, 8.28, 679.32, 8.28, "line"),
        Seg(678.48, 8.28, 679.32, 8.28, "line"),
        Seg(678.48, 8.28, 533.64, 10.20, "line"),
        Seg(678.60, 31.08, 533.52, 31.08, "line"),
        Seg(683.28, 12.60, 683.28, 27.36, "line"),
        Seg(681.12, 26.52, 681.12, 12.84, "line"),
        Seg(528.72, 26.64, 528.72, 12.72, "line"),
        Seg(530.88, 12.72, 530.88, 26.52, "line"),
        Seg(40.0, 200.0, 300.0, 200.0, "line"),
    ]
    words = [(550.0, 13.0, 665.0, 27.0, "CONFIDENTIAL")]
    report = exclude_sheet_metadata(segs, 841.92, 595.32, words)
    assert report["confidential_badge_segments_excluded"] == 8
    assert all(segs[i].kind == "sheet_metadata_badge" for i in range(8))
    assert segs[8].kind == "line"


def test_confidential_word_without_four_sided_badge_does_not_remove_wire():
    segs = [Seg(533.0, 8.0, 680.0, 8.0, "line")]
    words = [(550.0, 13.0, 665.0, 27.0, "CONFIDENTIAL")]
    report = exclude_sheet_metadata(segs, 841.92, 595.32, words)
    assert report["confidential_badge_segments_excluded"] == 0
    assert segs[0].kind == "line"
