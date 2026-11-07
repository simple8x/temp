"""Traceable parser-derived pin-to-net memberships.

This ledger exposes the parser's current connectivity decisions with primitive
lineage. It deliberately does NOT call those decisions independently verified.
"""
from __future__ import annotations

from .topology import terminal_status



def build_terminal_reference_map(netmap):
    """Return terminal-id -> unique parser ref; ambiguous multi-ref assignments stay unresolved."""
    candidates = {}
    for entries in netmap.values():
        for ref, terminal in entries:
            if ref:
                candidates.setdefault(id(terminal), set()).add(ref)
    return {
        terminal_id: next(iter(refs))
        for terminal_id, refs in candidates.items()
        if len(refs) == 1
    }


def resolve_terminal_ref(terminal, group_ref, reference_by_terminal):
    """Prefer topology group ownership; use production membership only as a unique fallback."""
    assigned = group_ref.get(terminal.group)
    if assigned:
        return assigned
    return reference_by_terminal.get(id(terminal))


def build_connectivity_evidence(topo, comps, netmap, pin_numbers=None, explicit_pin_numbers=None):
    pin_numbers = pin_numbers or {}
    explicit_pin_numbers = explicit_pin_numbers or {}

    def identity_kind(terminal):
        pin = pin_numbers.get(id(terminal))
        if pin is None:
            return "coordinate_fallback"
        if explicit_pin_numbers.get(id(terminal)) == pin:
            return "parser_pin_token"
        return "geometry_inferred"
    component_by_ref = {}
    for group in comps:
        component_by_ref.setdefault(group.ref, []).append(group)

    terminal_index = {id(terminal): index for index, terminal in enumerate(topo.terminals)}
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
            pin_identity_kind = identity_kind(terminal)
            pin_identity = pin if pin is not None else "%.2f_%.2f" % (terminal.x, terminal.y)
            memberships.append({
                "terminal_index": terminal_index.get(id(terminal)),
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

    # Preserve every topology terminal, including attached terminals that the
    # production net exporter omitted. The reference builder must not silently
    # lose terminals just because they have no production net membership.
    group_ref = {index: group.ref for index, group in enumerate(topo.groups) if group.ref}
    memberships_by_terminal = {}
    for row in memberships:
        memberships_by_terminal.setdefault(row.get("terminal_index"), []).append(row)
    terminal_inventory = []
    for index, terminal in enumerate(topo.terminals):
        wire_component = terminal.comp
        primitive_indices = [
            topo.W[k] for k in topo.comps[wire_component]
        ] if 0 <= wire_component < len(topo.comps) else []
        wire_primitives = []
        for primitive_index in primitive_indices:
            segment = topo.segs[primitive_index]
            wire_primitives.append({
                "primitive_index": primitive_index,
                "kind": segment.kind,
                "a": [round(segment.x0, 4), round(segment.y0, 4)],
                "b": [round(segment.x1, 4), round(segment.y1, 4)],
            })
        pin = pin_numbers.get(id(terminal))
        terminal_memberships = memberships_by_terminal.get(index, [])
        candidate_nets = sorted({row["net"] for row in terminal_memberships if row.get("net")})
        terminal_inventory.append({
            "terminal_index": index,
            "ref": group_ref.get(terminal.group),
            "pin_identity": pin,
            "pin_identity_kind": identity_kind(terminal) if pin is not None else "UNKNOWN",
            "x": round(terminal.x, 4),
            "y": round(terminal.y, 4),
            "status": terminal_status(terminal),
            "anchor_kind": terminal.anchor_kind or None,
            "symbol_group": terminal.group,
            "wire_component": wire_component,
            "wire_primitives": wire_primitives,
            "candidate_memberships": terminal_memberships,
            "candidate_net_names_diagnostic_only": candidate_nets,
            "candidate_net_status": (
                "SINGLE_PARSER_CANDIDATE" if len(candidate_nets) == 1 else
                "MULTIPLE_PARSER_CANDIDATES" if len(candidate_nets) > 1 else
                "NO_PRODUCTION_NET_MEMBERSHIP"
            ),
            "reference_status": "UNKNOWN",
        })

    # Inspect the full topology terminal inventory, not only component groups.
    # Unattached terminals are not included in Group.terminals by Topology._groups().
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
    no_connect_marks = [
        {
            "ref": group_ref.get(terminal.group),
            "pin_identity": pin_numbers.get(id(terminal), "%.2f_%.2f" % (terminal.x, terminal.y)),
            "pin_identity_kind": identity_kind(terminal),
            "x": round(terminal.nc_mark_x, 4),
            "y": round(terminal.nc_mark_y, 4),
            "terminal_x": round(terminal.x, 4),
            "terminal_y": round(terminal.y, 4),
            "distance": round(terminal.nc_mark_distance, 4),
            "wire_component": terminal.comp,
            "status": "EXPLICIT_NO_CONNECT_MARK",
        }
        for terminal in topo.terminals
        if terminal.kind == "nc"
    ]
    terminal_counts = {
        "all_topology_terminals": len(topo.terminals),
        "attached_terminals": sum(1 for terminal in topo.terminals if terminal.kind == "attach"),
        "explicit_no_connect_terminals": sum(1 for terminal in topo.terminals if terminal.kind == "nc"),
        "open_unresolved_terminals": sum(1 for terminal in topo.terminals if terminal.kind == "open"),
        "suppressed_nc_stub_body_ends": sum(1 for terminal in topo.terminals if terminal.kind == "nc_stub_body_end"),
        "nc_topology_conflict_terminals": sum(1 for terminal in topo.terminals if terminal.kind == "nc_topology_conflict"),
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
            "topology_terminal_inventory": len(terminal_inventory),
            "terminals_without_production_membership": sum(1 for row in terminal_inventory if not row["candidate_memberships"]),
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
        "terminal_inventory": terminal_inventory,
        "unresolved_terminals": unresolved,
        "no_connect_marks": no_connect_marks,
        "nc_stub_conflicts": getattr(topo, "nc_stub_conflicts", []),
        "pin_conflicts": pin_conflicts,
    }
