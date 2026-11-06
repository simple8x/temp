"""Traceable parser-derived pin-to-net memberships.

This ledger exposes the parser's current connectivity decisions with primitive
lineage. It deliberately does NOT call those decisions independently verified.
"""
from __future__ import annotations

from .topology import terminal_status


def build_connectivity_evidence(topo, comps, netmap, pin_numbers=None):
    pin_numbers = pin_numbers or {}
    component_by_ref = {}
    for group in comps:
        component_by_ref.setdefault(group.ref, []).append(group)

    memberships = []
    for net_name, entries in sorted(netmap.items()):
        for ref, terminal in entries:
            wire_component = terminal.comp
            segment_indices = [
                topo.W[k] for k in topo.comps[wire_component]
            ]
            segments = []
            for segment_index in segment_indices:
                segment = topo.segs[segment_index]
                segments.append({
                    "primitive_index": segment_index,
                    "kind": segment.kind,
                    "a": [round(segment.x0, 4), round(segment.y0, 4)],
                    "b": [round(segment.x1, 4), round(segment.y1, 4)],
                })
            pin = pin_numbers.get(id(terminal))
            pin_identity_kind = "parser_pin_token" if pin is not None else "coordinate_fallback"
            pin_identity = pin if pin is not None else "%.2f_%.2f" % (terminal.x, terminal.y)
            memberships.append({
                "net": net_name,
                "ref": ref,
                "pin_identity": pin_identity,
                "pin_identity_kind": pin_identity_kind,
                "terminal": {
                    "x": round(terminal.x, 4),
                    "y": round(terminal.y, 4),
                    "status": terminal_status(terminal),
                    "anchor_kind": terminal.anchor_kind or None,
                    "symbol_group": terminal.group,
                },
                "wire_component": wire_component,
                "wire_primitives": segments,
                "status": "PARSER_DERIVED_NOT_INDEPENDENTLY_VERIFIED",
                "independent_verification": "NOT_PERFORMED",
            })

    # Inspect the full topology terminal inventory, not only component groups.
    # Unattached terminals are not included in Group.terminals by Topology._groups().
    group_ref = {index: group.ref for index, group in enumerate(topo.groups) if group.ref}
    unresolved = [
        {
            "ref": group_ref.get(terminal.group),
            "x": round(terminal.x, 4),
            "y": round(terminal.y, 4),
            "wire_component": terminal.comp,
            "status": terminal_status(terminal),
            "component_assignment": "ASSIGNED" if terminal.group in group_ref else "UNRESOLVED",
        }
        for terminal in topo.terminals
        if terminal.kind == "open"
    ]
    terminal_counts = {
        "all_topology_terminals": len(topo.terminals),
        "attached_terminals": sum(1 for terminal in topo.terminals if terminal.kind == "attach"),
        "explicit_no_connect_terminals": sum(1 for terminal in topo.terminals if terminal.kind == "nc"),
        "open_unresolved_terminals": sum(1 for terminal in topo.terminals if terminal.kind == "open"),
    }

    conflicts = {}
    for row in memberships:
        key = (row["ref"], row["pin_identity"])
        conflicts.setdefault(key, set()).add(row["net"])
    pin_conflicts = [
        {"ref": ref, "pin_identity": pin, "nets": sorted(names)}
        for (ref, pin), names in sorted(conflicts.items())
        if len(names) > 1
    ]

    return {
        "schema": "pdf2kicad-connectivity-evidence/v1",
        "scope": "single_page_parser_output",
        "summary": {
            "parser_derived_memberships": len(memberships),
            "distinct_nets": len(netmap),
            **terminal_counts,
            "pin_identity_conflicts": len(pin_conflicts),
            "independently_verified_memberships": 0,
            "status": "PARSER_CONNECTIVITY_EXPOSED_INDEPENDENT_VERIFICATION_REQUIRED",
        },
        "policy": {
            "parser_memberships_are_not_ground_truth": True,
            "coordinate_fallback_is_not_a_pin_number": True,
            "open_terminal_is_not_no_connect": True,
            "independent_verification": "requires a separately derived reference topology or reviewed source-PDF pin/net evidence",
        },
        "memberships": memberships,
        "unresolved_terminals": unresolved,
        "pin_conflicts": pin_conflicts,
    }
