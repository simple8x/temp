"""Run provenance-preserving graph and terminal-membership diagnostics on the pinned QI9 PDF.

The graph is built from the exact normalized wire segments and junction dots
already consumed by the legacy Topology object. This avoids mixing raw PyMuPDF
page coordinates with scaled/rotated extractor coordinates. This report is
still not an independent electrical oracle and does not replace netlist export.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from pdf2kicad.cli import extract
from pdf2kicad.netlist import pin_tokens
from pdf2kicad.electrical_evidence import classify_page
from pdf2kicad.planar_graph import Primitive, build_planar_graph

PDF = Path("QI9-2604-A01_G3-G4_PAGE6_REFINEMENT/source/[QI9-2604-A01] Module IO.pdf")
PAGES = range(5, 12)
EXPECTED_SHA256 = "a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc"
NODE_TOLERANCE = 0.4


def _map_terminal(term, graph, component_by_node):
    """Map a physical terminal to one unique graph component; ambiguity stays UNKNOWN."""
    ranked = sorted(
        (math.hypot(node.x - term.x, node.y - term.y), node.node_id)
        for node in graph.nodes
    )
    if not ranked or ranked[0][0] > NODE_TOLERANCE:
        return {"decision": "UNKNOWN", "reason": "NO_GRAPH_NODE_WITHIN_TOLERANCE"}
    best_distance = ranked[0][0]
    tied = [node_id for distance, node_id in ranked
            if abs(distance - best_distance) <= 1e-7]
    components = {component_by_node[node_id] for node_id in tied}
    if len(components) != 1:
        return {"decision": "UNKNOWN", "reason": "AMBIGUOUS_GRAPH_COMPONENT_AT_TERMINAL",
                "candidate_nodes": tied}
    return {"decision": "MAPPED", "graph_component": next(iter(components)),
            "node_id": tied[0], "distance": round(best_distance, 6)}


def main():
    if not PDF.is_file():
        raise FileNotFoundError(f"pinned source PDF missing: {PDF}")
    digest = hashlib.sha256(PDF.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA256:
        raise RuntimeError(f"source PDF SHA256 mismatch: expected {EXPECTED_SHA256}, got {digest}")

    results = []
    any_empty_graph = False
    for page_no in PAGES:
        comps, legacy_nets, words, topo = extract(str(PDF), page_no, full=True)
        primitives = [
            Primitive(
                primitive_id=f"page{page_no}:seg{seg_idx}",
                x0=topo.segs[seg_idx].x0,
                y0=topo.segs[seg_idx].y0,
                x1=topo.segs[seg_idx].x1,
                y1=topo.segs[seg_idx].y1,
                kind=topo.segs[seg_idx].kind,
            )
            for seg_idx in topo.W
        ]

        # Critical: topo.dots and topo.segs share the normalized/scaled/rotated
        # coordinate frame. Raw PyMuPDF junction candidates are evidence only.
        graph = build_planar_graph(
            primitives,
            junction_dots=topo.dots,
            tolerance=NODE_TOLERANCE,
            dot_radius=1.0,
        )
        graph_components = graph.connected_components()
        component_by_node = {
            node_id: component_id
            for component_id, members in enumerate(graph_components)
            for node_id in members
        }
        pin_map, _ = pin_tokens(topo, comps, words)
        group_ref = {index: group.ref for index, group in enumerate(topo.groups) if group.ref}

        terminal_rows = []
        graph_memberships = {}
        unmapped = 0
        for terminal in topo.terminals:
            ref = group_ref.get(terminal.group, "")
            pin = pin_map.get(id(terminal), "")
            mapped = _map_terminal(terminal, graph, component_by_node)
            if mapped["decision"] != "MAPPED":
                unmapped += 1
            else:
                key = str(mapped["graph_component"])
                graph_memberships.setdefault(key, []).append({
                    "ref": ref, "pin": pin,
                    "terminal_xy": [round(terminal.x, 4), round(terminal.y, 4)],
                    "identity_status": "PRINTED_OR_GEOMETRIC_PIN_TOKEN" if pin else "PIN_IDENTITY_UNKNOWN",
                })
            terminal_rows.append({
                "ref": ref, "pin": pin,
                "terminal_xy": [round(terminal.x, 4), round(terminal.y, 4)],
                "terminal_status": terminal.kind,
                **mapped,
            })

        junction_report = classify_page(PDF, page_no)
        raw_confirmed = [
            tuple(mark["center_pt"])
            for mark in junction_report["junction_candidates"]
            if mark["status"] == "EXPLICIT_JUNCTION_CONFIRMED"
        ]
        any_empty_graph |= not graph.nodes or not graph.edges
        counts = {}
        for contact in graph.contacts:
            counts[contact.decision] = counts.get(contact.decision, 0) + 1

        results.append({
            "page": page_no,
            "wire_primitives": len(primitives),
            "normalized_junction_dots_applied": len(topo.dots),
            "raw_pdf_confirmed_junction_marks_evidence_only": len(raw_confirmed),
            "graph_nodes": len(graph.nodes),
            "graph_edges": len(graph.edges),
            "graph_components": len(graph_components),
            "contacts_by_decision": counts,
            "terminal_count": len(topo.terminals),
            "terminal_graph_mapped": len(topo.terminals) - unmapped,
            "terminal_graph_unmapped_or_ambiguous": unmapped,
            "graph_net_memberships": graph_memberships,
            "terminal_mapping_evidence": terminal_rows,
            "legacy_net_count": len(legacy_nets),
            "legacy_membership_count": sum(len(members) for members in legacy_nets.values()),
            "graph_vs_legacy_independent_comparison": "NOT_PERFORMED",
            "independent_reference_netlist": "NOT_AVAILABLE",
            "kicad_erc": "NOT_RUN",
            "status": "GRAPH_AND_TERMINAL_MEMBERSHIP_DIAGNOSTIC",
        })

    report = {
        "schema": "pdf2kicad-real-pdf-graph-membership-audit/v3",
        "source_repository": "simple8x/temp",
        "source_path": str(PDF),
        "source_sha256": digest,
        "source_sha256_check": "PASS",
        "pages": list(PAGES),
        "results": results,
        "overall_status": "BLOCKED_INDEPENDENT_ELECTRICAL_VERIFICATION_NOT_PERFORMED",
        "limitations": [
            "Graph uses normalized topo.segs and topo.dots in the same coordinate frame.",
            "Raw PyMuPDF junction evidence is not mixed into normalized graph coordinates.",
            "Terminal-to-graph mapping is nearest-node with a strict 0.4 coordinate tolerance; ties across components remain UNKNOWN.",
            "Graph memberships are diagnostic and do not yet drive the production netlist exporter.",
            "Legacy netlist is a comparison candidate, not an independent oracle.",
            "No independent reference netlist or KiCad ERC result is produced by this script.",
        ],
    }
    out = Path("planar_graph_real_pdf_audit.json")
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if any_empty_graph:
        raise SystemExit("FAIL: at least one audited page produced an empty planar graph")


if __name__ == "__main__":
    main()
