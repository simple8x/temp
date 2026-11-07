"""Run the planar graph as a diagnostic over the real QI9 PDF wire primitives.

This is intentionally not an independent netlist oracle and does not replace
the legacy parser. Confirmed junctions come only from electrical_evidence.py.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pdf2kicad.cli import extract
from pdf2kicad.electrical_evidence import classify_page
from pdf2kicad.planar_graph import Primitive, build_planar_graph

PDF = Path("QI9-2604-A01_G3-G4_PAGE6_REFINEMENT/source/[QI9-2604-A01] Module IO.pdf")
PAGES = range(5, 12)


def main():
    if not PDF.is_file():
        raise FileNotFoundError(f"pinned source PDF missing: {PDF}")
    digest = hashlib.sha256(PDF.read_bytes()).hexdigest()
    results = []
    for page_no in PAGES:
        _, nets, _, topo = extract(str(PDF), page_no, full=True)
        primitives = [
            Primitive(
                primitive_id=f"page{page_no}:seg{wire_idx}",
                x0=topo.segs[wire_idx].x0,
                y0=topo.segs[wire_idx].y0,
                x1=topo.segs[wire_idx].x1,
                y1=topo.segs[wire_idx].y1,
                kind=topo.segs[wire_idx].kind,
            )
            for wire_idx in topo.W
        ]
        junction_report = classify_page(PDF, page_no)
        confirmed = [
            tuple(mark["center_pt"])
            for mark in junction_report["junction_candidates"]
            if mark["status"] == "EXPLICIT_JUNCTION_CONFIRMED"
        ]
        graph = build_planar_graph(
            primitives,
            junction_dots=confirmed,
            tolerance=0.4,
            dot_radius=0.2,
        )
        counts = {}
        for contact in graph.contacts:
            counts[contact.decision] = counts.get(contact.decision, 0) + 1
        results.append({
            "page": page_no,
            "wire_primitives": len(primitives),
            "confirmed_junction_marks": len(confirmed),
            "graph_nodes": len(graph.nodes),
            "graph_edges": len(graph.edges),
            "graph_components": len(graph.connected_components()),
            "contacts_by_decision": counts,
            "legacy_wire_components": len(topo.comps),
            "parser_derived_memberships": sum(len(members) for members in nets.values()),
            "independent_netlist_verification": "NOT_PERFORMED",
            "status": "REAL_PDF_GEOMETRY_DIAGNOSTIC_ONLY",
        })
    report = {
        "schema": "pdf2kicad-planar-graph-real-pdf-audit/v1",
        "source_repository": "simple8x/temp",
        "source_path": str(PDF),
        "source_sha256": digest,
        "pages": list(PAGES),
        "results": results,
        "limitations": [
            "Diagnostic graph is not wired into netlist assignment or KiCad export.",
            "Only explicitly confirmed junction marks are supplied as connecting dots.",
            "This is not an independent electrical reference netlist or KiCad ERC.",
            "Legacy parser output is reported for comparison, not treated as ground truth.",
        ],
    }
    out = Path("planar_graph_real_pdf_audit.json")
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
