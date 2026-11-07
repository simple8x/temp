import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf
from pdf2kicad.pdf_reference_graph import build_pdf_reference_graph, compare_parser_memberships


def _pdf(path, with_dot=False):
    doc=pymupdf.open()
    page=doc.new_page(width=100,height=100)
    shape=page.new_shape()
    shape.draw_line((10,50),(90,50))
    shape.draw_line((50,10),(50,90))
    shape.finish(color=(0,0,0),width=.5)
    if with_dot:
        shape.draw_circle((50,50),1.2)
        shape.finish(color=(0,0,0),fill=(0,0,0))
    shape.commit()
    doc.save(path)
    doc.close()


def test_independent_pdf_graph_does_not_join_unmarked_crossing(tmp_path):
    path=tmp_path/"cross.pdf"
    _pdf(path,False)
    graph=build_pdf_reference_graph(str(path),1)
    assert len(graph["segments"]) == 3
    assert len(graph["components"]) == 2
    assert graph["scope"] == "INDEPENDENT_GEOMETRIC_CANDIDATE_NOT_ELECTRICAL_NETLIST"


def test_independent_pdf_graph_joins_explicit_dot_crossing(tmp_path):
    path=tmp_path/"dot.pdf"
    _pdf(path,True)
    graph=build_pdf_reference_graph(str(path),1)
    assert len(graph["components"]) == 1
    assert graph["intersections"][0]["decision"] == "JOIN"


def test_comparison_never_promotes_geometry_to_electrical_verification():
    graph={"segments":[{"id":0,"a":(0,0),"b":(10,0)}],"components":[[0]]}
    evidence={"memberships":[{"ref":"R1","pin_identity":"1","net":"N1",
                              "terminal_index":0,"terminal":{"x":5,"y":0}}]}
    result=compare_parser_memberships(graph,evidence)
    assert result["geometry_candidates"] == 1
    assert result["independently_verified_electrical_memberships"] == 0
    assert result["status"] == "DIAGNOSTIC_ONLY_ELECTRICAL_SIGNOFF_BLOCKED"
