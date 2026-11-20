#!/usr/bin/env python3
"""Run ECCI's fail-closed vector candidate pipeline on QI9 schematic pages 5-11.

This emits geometry and rule candidates only. It does not run the solver, create a
KiCad file, mutate a net, or promote any item to VERIFIED.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "claude"))

from ecci.classify import classify_page  # noqa: E402
from ecci.ingest_vector import ingest_pdf  # noqa: E402
from ecci.pins import PinParams, detect_pins  # noqa: E402
from ecci.rules import RulesParams, build_conn_graph, find_dots, off_colour_body  # noqa: E402


def run(pdf: Path) -> dict:
    source_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
    ingested = ingest_pdf(str(pdf), pages=list(range(4, 11)), flatten_tol=0.05)
    pages: list[dict] = []
    all_dots: list[dict] = []
    all_edges: list[dict] = []
    all_sites: list[dict] = []
    all_unresolved: list[dict] = []
    totals = collections.Counter()

    for pi in ingested.pages:
        cls = classify_page(pi)
        dots = find_dots(pi, cls, RulesParams())
        clusters = detect_pins(
            pi, cls, [], PinParams(), RulesParams().eps,
            frozenset(v[2] for v in dots.values()) | off_colour_body(pi, cls),
        )
        built = build_conn_graph(pi, cls, clusters, RulesParams(), dots)
        by_id = {p.id: p for p in pi.prims}
        rule_counts = collections.Counter(e.rule for e in built.graph.edges)
        attached = sum(v[2] is not None for v in built.dots.values())
        unresolved = [u for c in clusters for u in c.unresolved]
        page_no = pi.geom.page_index + 1
        class_counts = {
            name: sum(record.cls == name for record in cls.records.values())
            for name in ("WIRE", "BODY", "TEXT", "BORDER", "OTHER")
        }
        page_record = {
            "page": page_no,
            "primitives": len(pi.prims),
            "class_counts": class_counts,
            "body_clusters": len(clusters),
            "unique_symbol_matches": sum(c.resolution.status == "UNIQUE" for c in clusters),
            "junction_dot_candidates": len(dots),
            "dots_attached_to_wire_nodes": attached,
            "candidate_edges": len(built.graph.edges),
            "candidate_rules": dict(sorted(rule_counts.items())),
            "ambiguity_sites": len(built.graph.sites),
            "unresolved_terminal_contact_records": len(unresolved),
            "unexpected_body_contacts": sum(len(c.conflicts) for c in clusters),
            "ingest_unsupported": pi.report.unsupported,
            "ingest_errors": pi.report.errors,
        }
        pages.append(page_record)
        for key, value in {
            "primitives": len(pi.prims),
            "wire_primitives": class_counts["WIRE"],
            "body_clusters": len(clusters),
            "junction_dot_candidates": len(dots),
            "dots_attached_to_wire_nodes": attached,
            "candidate_edges": len(built.graph.edges),
            "ambiguity_sites": len(built.graph.sites),
            "unresolved_terminal_contact_records": len(unresolved),
            **{f"rule_{k}": v for k, v in rule_counts.items()},
        }.items():
            totals[key] += value

        for did, (center, radius, prim_id) in sorted(dots.items()):
            all_dots.append({
                "page": page_no,
                "dot_id": did,
                "source_primitive_id": prim_id,
                "source_content_ref": by_id[prim_id].prov.content_ref,
                "center_page_points": [round(center[0], 6), round(center[1], 6)],
                "radius_page_points": round(radius, 6),
                "attached_wire_node": built.dots[did][2],
                "status": "CANDIDATE_NOT_PROMOTED",
            })

        for edge in built.graph.edges:
            all_edges.append({
                "page": page_no,
                "id": edge.id,
                "rule": edge.rule,
                "a": edge.a,
                "b": edge.b,
                "coord": edge.coord,
                "n_prims": edge.n_prims,
                "roles": [{
                    "obj": role.obj,
                    "kind": role.kind,
                    "status": role.status,
                    "evidence": list(role.evidence),
                } for role in edge.roles],
                "provenance": list(edge.prov),
                "status": "CANDIDATE_NOT_PROMOTED",
            })

        for site in built.graph.sites:
            all_sites.append({
                "page": page_no,
                "id": site.id,
                "kind": site.kind,
                "arms": [list(arm) for arm in site.arms],
                "options": [option.name for option in site.options],
                "status": "UNKNOWN",
            })

        for cluster in clusters:
            for item in cluster.unresolved:
                prim = by_id[item.wire_prim]
                all_unresolved.append({
                    "page": page_no,
                    "cluster_id": item.cluster_id,
                    "contact_page_points": list(item.contact),
                    "far_end_page_points": list(item.far_end),
                    "wire_primitive_id": item.wire_prim,
                    "source_content_ref": prim.prov.content_ref,
                    "evidence_id": item.evidence,
                    "status": "UNKNOWN",
                })

    return {
        "schema": "qi9.ecci.vector-candidate-connectivity.v1",
        "source_pdf_sha256": source_hash,
        "extractor": "ECCI PDF vector interpreter + classify_page + find_dots + build_conn_graph",
        "parameters": {"schematic_pages": "5-11", "flatten_tol": 0.05, "arrangement_eps": 0.02},
        "pages": pages,
        "totals": dict(sorted(totals.items())),
        "junction_dot_candidates": all_dots,
        "candidate_edges": all_edges,
        "ambiguity_sites": all_sites,
        "unresolved_terminal_contact_records": all_unresolved,
        "authority": {
            "status": "CANDIDATE_ONLY",
            "connectivity_claims": 0,
            "verified_items": 0,
            "may_create_or_modify_nets": False,
            "may_promote_verified": False,
            "solver_promotion_executed": False,
            "symbol_library_loaded": False,
            "reference_to_symbol_mapping": "UNKNOWN",
            "pin_number_mapping": "UNKNOWN",
        },
        "blockers": [
            "No source KiCad symbol library was supplied; reference/body identity and pin numbers remain UNKNOWN.",
            "The vector importer reports clip_not_applied and text_show_ops on every schematic page.",
            "Net labels, power symbols, hierarchical labels and no-connect markers are not extracted by this vector-only pass.",
            "TR1/TR2/TR4/TR5 records are candidates only; no solver or writer was run.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.pdf.is_file():
        parser.error(f"PDF not found: {args.pdf}")
    report = run(args.pdf)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "source_pdf_sha256": report["source_pdf_sha256"],
                      "totals": report["totals"], "authority": report["authority"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
[exit status 0]