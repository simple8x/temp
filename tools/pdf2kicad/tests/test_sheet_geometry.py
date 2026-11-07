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
        Seg(330, 650, 590, 650, "line"),
        Seg(590, 650, 590, 790, "line"),
        Seg(590, 790, 330, 790, "line"),
        Seg(330, 790, 330, 650, "line"),
        Seg(340, 720, 580, 720, "line"),  # internal table rule
        Seg(340, 100, 580, 100, "line"),  # unrelated circuit wire
    ]
    words = [
        (350, 670, 390, 680, "PROJECT"),
        (400, 750, 450, 760, "REV"),
    ]
    report = exclude_sheet_metadata(segs, 600, 800, words)
    assert report["title_block_segments_excluded"] == 5
    assert all(segs[i].kind == "title_block" for i in range(5))
    assert segs[5].kind == "line"


def test_open_box_or_box_without_text_is_not_excluded():
    segs = [
        Seg(330, 650, 590, 650, "line"),
        Seg(590, 650, 590, 790, "line"),
        Seg(590, 790, 330, 790, "line"),
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
