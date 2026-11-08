import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pdf_symbol_body_candidates import _associate


def test_reference_above_rectangular_symbol():
    r=_associate([10,10,16,14],[8,25,30,55])
    assert r and r["mode"]=="REFERENCE_ABOVE_SHAPE"


def test_reference_to_right_of_connector_body():
    r=_associate([40,25,46,30],[15,10,36,45])
    assert r and r["mode"]=="REFERENCE_SIDE_OF_SHAPE"


def test_distant_shape_not_associated():
    assert _associate([10,10,16,14],[80,80,100,110]) is None


def test_overlap_is_candidate_only_geometry():
    r=_associate([10,10,16,14],[12,12,30,35])
    assert r and r["mode"]=="OVERLAPPING"
