"""Graph-derived net membership adapter for the PDF vector extractor.

Wire membership comes from the provenance-preserving planar graph. The legacy
netlist is consulted only for already-detected label names; it is never used to
decide which wire primitives are connected. Ambiguous mappings/label conflicts
fail closed rather than silently exporting a guessed netlist.
"""
from __future__ import annotations

from collections import defaultdict
import math

from .planar_graph import Primitive, build_planar_graph


class GraphConnectivityError(RuntimeError):
    """The graph cannot safely assign every referenced terminal to one net."""


def graph_nets(topo, comps, legacy_nets, *, tolerance=0.4, dot_radius=1.0):
    """Return a net-name mapping using graph connectivity.

    The topology stage supplies candidate electrical wire primitives and
    symbol/terminal provenance. The planar graph resolves their geometric
    contacts. Existing net labels are naming/equivalence hints only.
    """
    wire_indices = sorted({
        k for component in topo.wire_comps for k in topo.comps[component]
    })
    primitives = [
        Primitive(
            primitive_id=f"wire{k}",
            x0=topo.segs[topo.W[k]].x0,
            y0=topo.segs[topo.W[k]].y0,
            x1=topo.segs[topo.W[k]].x1,
            y1=topo.segs[topo.W[k]].y1,
            kind=topo.segs[topo.W[k]].kind,
        )
        for k in wire_indices
    ]
    if not primitives:
        if any(g.ref and g.terminals for g in comps):
            raise GraphConnectivityError("no electrical wire primitives for referenced terminals")
        return {}

    graph = build_planar_graph(
        primitives, junction_dots=topo.dots,
        tolerance=tolerance, dot_radius=dot_radius,
    )
    graph_components = graph.connected_components()
    component_by_node = {
        node_id: component_id
        for component_id, members in enumerate(graph_components)
        for node_id in members
    }

    def map_terminal(terminal):
        if terminal.comp < 0 or terminal.comp >= len(topo.comps):
            return None
        allowed = {f"wire{k}" for k in topo.comps[terminal.comp]}
        ranked = sorted(
            (math.hypot(node.x - terminal.x, node.y - terminal.y), node.node_id)
            for node in graph.nodes if node.provenance.intersection(allowed)
        )
        if not ranked or ranked[0][0] > tolerance:
            return None
        best = ranked[0][0]
        tied = [node_id for distance, node_id in ranked if abs(distance - best) <= 1e-7]
        owners = {component_by_node[node_id] for node_id in tied}
        if len(owners) != 1:
            return None
        return next(iter(owners))

    label_by_terminal = {}
    for label, members in legacy_nets.items():
        for _ref, terminal in members:
            previous = label_by_terminal.get(id(terminal))
            if previous is not None and previous != label:
                raise GraphConnectivityError(
                    f"terminal has conflicting legacy labels: {previous!r}, {label!r}"
                )
            label_by_terminal[id(terminal)] = label

    parent = list(range(len(graph_components)))

    def find(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left, right):
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    terminal_rows = []
    physical_pin_components = defaultdict(set)
    for group in comps:
        if not group.ref:
            continue
        for terminal in group.terminals:
            if terminal.kind in ("nc", "nc_stub_body_end", "nc_topology_conflict"):
                continue
            component_id = map_terminal(terminal)
            if component_id is None:
                raise GraphConnectivityError(
                    f"UNKNOWN graph mapping for {group.ref} terminal "
                    f"({terminal.x:.3f},{terminal.y:.3f}) from wire component {terminal.comp}"
                )
            terminal_rows.append((group.ref, terminal, component_id))
            physical_pin_components[(group.ref, round(terminal.x, 2), round(terminal.y, 2))].add(component_id)

    for owners in physical_pin_components.values():
        owners = sorted(owners)
        for other in owners[1:]:
            union(owners[0], other)

    first_component_for_label = {}
    for _ref, terminal, component_id in terminal_rows:
        label = label_by_terminal.get(id(terminal))
        if not label:
            continue
        root = find(component_id)
        if label in first_component_for_label:
            union(root, first_component_for_label[label])
        else:
            first_component_for_label[label] = root

    labels_by_root = defaultdict(set)
    for _ref, terminal, component_id in terminal_rows:
        label = label_by_terminal.get(id(terminal))
        if label:
            labels_by_root[find(component_id)].add(label)
    conflicts = {root: sorted(labels) for root, labels in labels_by_root.items() if len(labels) > 1}
    if conflicts:
        raise GraphConnectivityError(f"conflicting explicit net labels on graph components: {conflicts}")

    out = defaultdict(list)
    seen = set()
    for ref, terminal, component_id in terminal_rows:
        root = find(component_id)
        labels = labels_by_root.get(root, set())
        name = next(iter(labels)) if labels else f"N$GRAPH{root}"
        member = (name, ref, round(terminal.x, 2), round(terminal.y, 2))
        if member not in seen:
            seen.add(member)
            out[name].append((ref, terminal))
    return dict(out)
