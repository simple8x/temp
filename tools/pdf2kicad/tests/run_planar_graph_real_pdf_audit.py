"""Audit production graph extraction and rebuild a diagnostic graph on the pinned QI9 PDF.

The diagnostic graph pass checks geometry/mapping; it is not an independent
electrical oracle or KiCad ERC.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

from pdf2kicad.cli import extract
from pdf2kicad.netlist import pin_tokens, values as extract_values, infer_semantic_pin_identities, pin_identity_provenance
from pdf2kicad.electrical_evidence import classify_page
from pdf2kicad.connectivity_evidence import build_terminal_reference_map, resolve_terminal_ref
from pdf2kicad.planar_graph import Primitive, build_planar_graph

PDF = Path("QI9-2604-A01_G3-G4_PAGE6_REFINEMENT/source/[QI9-2604-A01] Module IO.pdf")
PAGES = range(5, 12)
EXPECTED_SHA256 = "a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc"
NODE_TOLERANCE = 0.4


def _map_terminal(term, topo, graph, component_by_node):
    """Map only against nodes carrying provenance from this terminal's own wire component."""
    wire_indices = topo.comps[term.comp] if 0 <= term.comp < len(topo.comps) else []
    allowed_ids = {f"page{topo.page_no}:wire{k}" for k in wire_indices}
    ranked = sorted(
        (math.hypot(node.x - term.x, node.y - term.y), node.node_id)
        for node in graph.nodes if node.provenance.intersection(allowed_ids)
    )
    if not ranked or ranked[0][0] > NODE_TOLERANCE:
        return {"decision": "UNKNOWN", "reason": "NO_NODE_FROM_TERMINAL_WIRE_COMPONENT"}
    best_distance = ranked[0][0]
    tied = [node_id for distance, node_id in ranked if abs(distance - best_distance) <= 1e-7]
    components = {component_by_node[node_id] for node_id in tied}
    if len(components) != 1:
        return {"decision": "UNKNOWN", "reason": "AMBIGUOUS_COMPONENT_ON_TERMINAL_WIRE",
                "candidate_nodes": tied}
    return {"decision": "MAPPED", "graph_component": next(iter(components)),
            "node_id": tied[0], "distance": round(best_distance, 6)}


def main():
    if not PDF.is_file():
        raise FileNotFoundError(f"pinned source PDF missing: {PDF}")
    digest = hashlib.sha256(PDF.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA256:
        raise RuntimeError(f"source PDF SHA256 mismatch: expected {EXPECTED_SHA256}, got {digest}")

    results, candidate_rows, production_rows, review_rows, review_page_evidence = [], [], [], [], []
    any_empty_graph = False
    any_component_legacy_conflict = False
    for page_no in PAGES:
        comps, production_nets, words, topo = extract(str(PDF), page_no, full=True)
        topo.page_no = page_no
        # Exclude symbol-body and inner-artwork components. The legacy Topology
        # classifier already separates these from electrical wire components.
        wire_indices = sorted({k for component in topo.wire_comps for k in topo.comps[component]})
        primitives = [
            Primitive(
                primitive_id=f"page{page_no}:wire{k}",
                x0=topo.segs[topo.W[k]].x0, y0=topo.segs[topo.W[k]].y0,
                x1=topo.segs[topo.W[k]].x1, y1=topo.segs[topo.W[k]].y1,
                kind=topo.segs[topo.W[k]].kind,
            )
            for k in wire_indices
        ]
        graph = build_planar_graph(
            primitives, junction_dots=topo.dots,
            tolerance=NODE_TOLERANCE, dot_radius=1.0,
        )
        graph_components = graph.connected_components()
        component_by_node = {
            node_id: component_id
            for component_id, members in enumerate(graph_components)
            for node_id in members
        }
        primitive_by_id = {primitive.primitive_id: primitive for primitive in primitives}
        primitive_ids_by_component = {
            component_id: sorted({
                primitive_id
                for node_id in members
                for primitive_id in graph.nodes[node_id].provenance
            })
            for component_id, members in enumerate(graph_components)
        }
        review_page_evidence.append({
            "page": page_no,
            "coordinate_system": "source PDF points",
            "primitive_catalog": {
                primitive.primitive_id: {
                    "a": [round(primitive.x0, 4), round(primitive.y0, 4)],
                    "b": [round(primitive.x1, 4), round(primitive.y1, 4)],
                    "kind": primitive.kind,
                }
                for primitive in primitives
            },
            "graph_component_primitive_ids": {
                str(component_id): primitive_ids
                for component_id, primitive_ids in primitive_ids_by_component.items()
            },
        })
        pin_map, claimed_pin_words = pin_tokens(topo, comps, words)
        value_map = extract_values(topo, comps, words, claimed_pin_words)
        pin_map, explicit_pin_map, inferred_pin_ids, symbol_map = infer_semantic_pin_identities(
            topo, comps, words, pin_map, value_map
        )
        group_ref = {index: group.ref for index, group in enumerate(topo.groups) if group.ref}
        # Production net tuples carry a ref for terminals whose topology.group
        # index has no direct symbol-group entry. Accept only a unique ref.
        reference_by_terminal = build_terminal_reference_map(production_nets)

        # Legacy names are used only as a diagnostic comparator, never as graph connectivity input.
        production_net_by_terminal = {}
        for net_name, members in production_nets.items():
            for ref, terminal in members:
                production_net_by_terminal[id(terminal)] = net_name

        terminal_rows, graph_memberships = [], {}
        unmapped = 0
        for terminal in topo.terminals:
            ref = resolve_terminal_ref(terminal, group_ref, reference_by_terminal) or ""
            pin = pin_map.get(id(terminal), "")
            pin_provenance = pin_identity_provenance(
                terminal, pin if pin else None, explicit_pin_map, inferred_pin_ids
            )
            mapped = _map_terminal(terminal, topo, graph, component_by_node)
            legacy_name = production_net_by_terminal.get(id(terminal), "")
            if mapped["decision"] != "MAPPED":
                unmapped += 1
            else:
                key = str(mapped["graph_component"])
                item = {
                    "ref": ref, "pin": pin, "legacy_net_name": legacy_name,
                    "terminal_xy": [round(terminal.x, 4), round(terminal.y, 4)],
                    "pin_identity_status": "KNOWN" if pin else "UNKNOWN",
                    "pin_identity_provenance": pin_provenance,
                    "evidence_classification": "INFERRED" if pin else "UNKNOWN",
                }
                graph_memberships.setdefault(key, []).append(item)
                candidate_rows.append([
                    page_no, mapped["graph_component"], ref, pin,
                    "KNOWN" if pin else "UNKNOWN", legacy_name,
                    f"{terminal.x:.4f}", f"{terminal.y:.4f}",
                    mapped["node_id"], mapped["distance"],
                ])
            if mapped["decision"] != "MAPPED":
                classification = "UNKNOWN_GRAPH_MAPPING"
            elif not ref and terminal.kind == "open":
                classification = "OPEN_WIRE_ENDPOINT"
            elif not ref and terminal.kind == "nc_stub_body_end":
                classification = "NC_STUB_BODY_END"
            elif not ref:
                classification = "UNREFERENCED_SYMBOL_OR_WIRE_TERMINAL"
            elif not pin:
                classification = "PIN_IDENTITY_UNKNOWN"
            else:
                classification = "INFERRED"
            terminal_row = {
                "ref": ref, "pin": pin, "pin_identity_provenance": pin_provenance,
                "legacy_net_name": legacy_name,
                "terminal_xy": [round(terminal.x, 4), round(terminal.y, 4)],
                "terminal_status": terminal.kind,
                "evidence_classification": classification,
                **mapped,
            }
            terminal_rows.append(terminal_row)
            # Every topology terminal needs a PDF review record. Parser-inferred ref/pin
            # candidates are not independent reference truth and must not be omitted.
            component_id = mapped.get("graph_component")
            node_id = mapped.get("node_id")
            source_ids = (
                sorted(graph.nodes[node_id].provenance)
                if node_id is not None else []
            )
            review_rows.append({
                "page": page_no, "ref_candidate": ref or None,
                "pin_candidate": pin or None, "pin_candidate_source_kind": pin_provenance,
                "candidate_net_name_diagnostic_only": legacy_name or None,
                "terminal_xy_pdf_pt": [round(terminal.x, 4), round(terminal.y, 4)],
                "terminal_status": terminal.kind, "classification": classification,
                "graph_component_candidate": component_id,
                "graph_node_candidate": mapped.get("node_id"),
                "mapping_decision": mapped["decision"],
                "mapping_distance_pt": mapped.get("distance"),
                "source_wire_primitive_ids": source_ids,
                "reference_pin": None, "reference_net": None,
                "reference_status": "UNKNOWN",
                "review_status": "REQUIRES_HUMAN_REVIEW_AGAINST_SOURCE_PDF",
                "promotion_policy": "NEVER_AUTO_PROMOTE_TO_VERIFIED",
            })

        # If one graph component contains multiple legacy net names, graph and legacy
        # disagree on separation. Keep this as a hard audit finding, not a silent merge.
        conflicting_components = {
            component: sorted({m["legacy_net_name"] for m in members if m["legacy_net_name"]})
            for component, members in graph_memberships.items()
        }
        conflicting_components = {
            component: names for component, names in conflicting_components.items()
            if len(names) > 1
        }
        any_component_legacy_conflict |= bool(conflicting_components)

        # A graph/legacy disagreement is a CONFLICT, not a reason to pick one.
        # Matching legacy output is not CORROBORATION: both paths share parser
        # topology/label evidence. Only an external reviewed reference can promote
        # a terminal to CORROBORATED or VERIFIED.
        conflict_ids = {int(component) for component in conflicting_components}
        for row in terminal_rows:
            component = row.get("graph_component")
            if component is not None and component in conflict_ids:
                row["evidence_classification"] = "CONFLICT"
        for row in review_rows:
            if row["page"] == page_no and row["graph_component_candidate"] in conflict_ids:
                row["classification"] = "CONFLICT"
        for component, members in graph_memberships.items():
            if int(component) in conflict_ids:
                for member in members:
                    member["evidence_classification"] = "CONFLICT"

        # A legacy net split across graph components can be a global/local label
        # propagation requirement; record it separately instead of guessing a wire join.
        graph_components_by_legacy = {}
        for component, members in graph_memberships.items():
            for name in {m["legacy_net_name"] for m in members if m["legacy_net_name"]}:
                graph_components_by_legacy.setdefault(name, set()).add(component)
        split_legacy_nets = {
            name: sorted(components_for_name)
            for name, components_for_name in graph_components_by_legacy.items()
            if len(components_for_name) > 1
        }

        for net_name, members in production_nets.items():
            for ref, terminal in members:
                pin = pin_map.get(id(terminal), "")
                production_rows.append([
                    page_no, net_name, ref, pin, "KNOWN" if pin else "UNKNOWN",
                    f"{terminal.x:.4f}", f"{terminal.y:.4f}",
                ])

        junction_report = classify_page(PDF, page_no)
        raw_confirmed = sum(
            1 for mark in junction_report["junction_candidates"]
            if mark["status"] == "EXPLICIT_JUNCTION_CONFIRMED"
        )
        any_empty_graph |= not graph.nodes or not graph.edges
        counts = {}
        for contact in graph.contacts:
            counts[contact.decision] = counts.get(contact.decision, 0) + 1

        results.append({
            "page": page_no,
            "sheet_metadata_filter": getattr(topo, "sheet_metadata_filter", {
                "sheet_frame_segments_excluded": 0,
                "title_block_segments_excluded": 0,
            }),
            "wire_primitives": len(primitives),
            "topology_wire_components": len(topo.wire_comps),
            "body_or_inner_components_excluded": len(topo.comps) - len(topo.wire_comps),
            "normalized_junction_dots_applied": len(topo.dots),
            "raw_pdf_confirmed_junction_marks_evidence_only": raw_confirmed,
            "graph_nodes": len(graph.nodes), "graph_edges": len(graph.edges),
            "graph_components": len(graph_components),
            "contacts_by_decision": counts,
            "terminal_count": len(topo.terminals),
            "terminal_graph_mapped": len(topo.terminals) - unmapped,
            "terminal_graph_unmapped_or_ambiguous": unmapped,
            "graph_net_memberships": graph_memberships,
            "graph_components_conflicting_with_legacy_net_names": conflicting_components,
            "legacy_nets_split_across_graph_components": split_legacy_nets,
            "terminal_mapping_evidence": terminal_rows,
            "production_graph_net_count": len(production_nets),
            "production_graph_membership_count": sum(len(members) for members in production_nets.values()),
            "graph_vs_production_comparison": "SAME_BACKEND_DIAGNOSTIC_PARITY_ONLY",
            "independent_reference_netlist": "NOT_AVAILABLE",
            "kicad_erc": "NOT_RUN",
            "status": "GRAPH_DERIVED_PRODUCTION_EXTRACT_NOT_ELECTRICALLY_VERIFIED",
        })

    expected_review_count = sum(x["terminal_count"] for x in results)
    if len(review_rows) != expected_review_count:
        raise RuntimeError(f"reference review coverage incomplete: {len(review_rows)} / {expected_review_count} topology terminals")
    if any(row["reference_status"] != "UNKNOWN" for row in review_rows):
        raise RuntimeError("parser output must never be promoted to reference truth")

    production_path = Path("production_graph_net_memberships.csv")
    with production_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["page", "net", "ref", "pin_identity", "pin_identity_status", "terminal_x", "terminal_y"])
        writer.writerows(production_rows)

    review_json_path = Path("reference_connectivity_review.json")
    review_doc = {
        "schema": "pdf2kicad-pdf-reference-connectivity-review/v1",
        "source_pdf": str(PDF),
        "source_pdf_sha256": digest,
        "pages": list(PAGES),
        "summary": {
            "review_entries": len(review_rows),
            "reference_status": "UNKNOWN_FOR_ALL_ENTRIES",
            "auto_verified": 0,
            "human_pdf_review_required": len(review_rows),
        },
        "policy": {
            "graph_membership_is_candidate_evidence_not_reference_truth": True,
            "coordinate_fallback_is_not_a_pin_number": True,
            "unreferenced_graph_terminals_are_not_assumed_component_pins": True,
            "open_wire_end_is_not_no_connect": True,
            "nc_stub_body_end_is_not_a_verified_nc_pin": True,
            "no_automatic_promotion_to_verified": True,
        },
        "page_evidence": review_page_evidence,
        "entries": review_rows,
    }
    review_json_path.write_text(json.dumps(review_doc, indent=2) + "\n", encoding="utf-8")
    review_csv_path = Path("reference_connectivity_review.csv")
    with review_csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "page", "ref_candidate", "pin_candidate", "candidate_net_name_diagnostic_only",
            "terminal_xy_pdf_pt", "terminal_status", "classification",
            "graph_component_candidate", "graph_node_candidate", "mapping_decision",
            "mapping_distance_pt", "source_wire_primitive_ids", "reference_pin",
            "reference_net", "reference_status", "review_status", "promotion_policy",
        ])
        writer.writeheader()
        for row in review_rows:
            csv_row = {key: row.get(key) for key in writer.fieldnames}
            csv_row["source_wire_primitive_ids"] = ";".join(row["source_wire_primitive_ids"])
            csv_row["terminal_xy_pdf_pt"] = json.dumps(row["terminal_xy_pdf_pt"])
            writer.writerow(csv_row)

    candidate_path = Path("graph_netlist_candidate.csv")
    with candidate_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "page", "graph_component", "ref", "pin", "pin_identity_status",
            "legacy_net_name_diagnostic_only", "terminal_x", "terminal_y",
            "graph_node_id", "mapping_distance",
        ])
        writer.writerows(candidate_rows)

    report = {
        "schema": "pdf2kicad-real-pdf-graph-membership-audit/v5",
        "source_repository": "simple8x/temp", "source_path": str(PDF),
        "source_sha256": digest, "source_sha256_check": "PASS",
        "pages": list(PAGES), "results": results,
        "summary": {
            "wire_primitives": sum(x["wire_primitives"] for x in results),
            "graph_nodes": sum(x["graph_nodes"] for x in results),
            "graph_edges": sum(x["graph_edges"] for x in results),
            "graph_components": sum(x["graph_components"] for x in results),
            "terminals": sum(x["terminal_count"] for x in results),
            "terminals_mapped": sum(x["terminal_graph_mapped"] for x in results),
            "terminals_unmapped": sum(x["terminal_graph_unmapped_or_ambiguous"] for x in results),
            "evidence_classification_counts": {
                status: sum(
                    1 for page_result in results
                    for row in page_result["terminal_mapping_evidence"]
                    if row["evidence_classification"] == status
                )
                for status in (
                    "VERIFIED", "CORROBORATED", "INFERRED", "UNKNOWN_GRAPH_MAPPING",
                    "PIN_IDENTITY_UNKNOWN", "UNREFERENCED_SYMBOL_OR_WIRE_TERMINAL",
                    "OPEN_WIRE_ENDPOINT", "NC_STUB_BODY_END", "CONFLICT",
                )
            },
            "topology_terminal_count": sum(x["terminal_count"] for x in results),
            "reference_connectivity_review_entries": len(review_rows),
            "reference_review_coverage_complete": len(review_rows) == sum(x["terminal_count"] for x in results),
            "reference_review_status_all_unknown": all(row["reference_status"] == "UNKNOWN" for row in review_rows),
            "reference_connectivity_auto_verified": 0,
            "candidate_netlist_rows": len(candidate_rows),
            "production_graph_membership_rows": len(production_rows),
            "production_graph_pin_identity_known": sum(1 for row in production_rows if row[4] == "KNOWN"),
            "production_graph_pin_identity_unknown": sum(1 for row in production_rows if row[4] == "UNKNOWN"),
            "graph_component_legacy_conflict_present": any_component_legacy_conflict,
        },
        "overall_status": "GRAPH_DERIVED_CANDIDATES_EXTRACTED_REFERENCE_REVIEW_REQUIRED_NO_AUTO_VERIFICATION",
        "limitations": [
            "Graph uses only Topology-classified electrical wire components; symbol-body and inner-artwork components are excluded. Normalized junction dots are applied.",
            "Terminal mapping is constrained by exact source-wire-component primitive provenance and 0.4 coordinate tolerance.",
            "Graph candidate CSV preserves UNKNOWN pin identities; graph_component is not a KiCad net name.",
            "Graph geometry determines wire membership in the production extract path; the existing label detector supplies net names/equivalence hints only.",
            "Global/local label and power-symbol equivalence still depend on the existing label detector and are not independently parsed by the graph engine.",
            "This audit exercises cli.extract() graph connectivity but does not invoke the CLI file writer or KiCad ERC.",
            "No independent reference netlist or KiCad ERC result is available in this audit.",
            "reference_connectivity_review.json contains PDF-coordinate and source-wire-primitive evidence for every non-INFERRED terminal; it is a review ledger, not an approved reference netlist.",
        ],
    }
    Path("planar_graph_real_pdf_audit.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    if any_empty_graph:
        raise SystemExit("FAIL: at least one audited page produced an empty planar graph")


if __name__ == "__main__":
    main()
