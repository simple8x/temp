"""Provenance-preserving planar graph prototype.

Diagnostic-only: deliberately separate from the legacy netlist pipeline.
Coordinates are PDF points. Ambiguous T contacts stay UNKNOWN unless a
confirmed explicit junction mark supports the connection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
import math
from typing import Iterable


@dataclass(frozen=True)
class Primitive:
    primitive_id: str
    x0: float
    y0: float
    x1: float
    y1: float
    kind: str = "wire"

    @property
    def length(self) -> float:
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)


@dataclass
class Node:
    node_id: int
    x: float
    y: float
    provenance: set[str] = field(default_factory=set)
    decisions: list[str] = field(default_factory=list)


@dataclass
class Edge:
    edge_id: int
    start: int
    end: int
    primitive_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Contact:
    x: float
    y: float
    primitive_a: str
    primitive_b: str
    kind: str
    decision: str
    reason: str


@dataclass
class PlanarGraph:
    nodes: list[Node]
    edges: list[Edge]
    contacts: list[Contact]
    tolerance: float

    def connected_components(self) -> list[set[int]]:
        parent = list(range(len(self.nodes)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for edge in self.edges:
            parent[find(edge.start)] = find(edge.end)
        groups = {}
        for node in self.nodes:
            groups.setdefault(find(node.node_id), set()).add(node.node_id)
        return list(groups.values())


def _cross(ax, ay, bx, by):
    return ax * by - ay * bx


def _point_on_segment(px, py, s, tol):
    dx, dy = s.x1 - s.x0, s.y1 - s.y0
    ll = dx * dx + dy * dy
    if ll == 0:
        return None
    t = ((px - s.x0) * dx + (py - s.y0) * dy) / ll
    eps = tol / max(s.length, tol)
    if t < -eps or t > 1 + eps:
        return None
    qx, qy = s.x0 + t * dx, s.y0 + t * dy
    if math.hypot(px - qx, py - qy) <= tol:
        return max(0.0, min(1.0, t))
    return None


def _intersection(a, b, tol):
    ax, ay = a.x1 - a.x0, a.y1 - a.y0
    bx, by = b.x1 - b.x0, b.y1 - b.y0
    den = _cross(ax, ay, bx, by)
    qx, qy = b.x0 - a.x0, b.y0 - a.y0
    scale = max(a.length * b.length, 1.0)
    if abs(den) > 1e-10 * scale:
        ta = _cross(qx, qy, bx, by) / den
        tb = _cross(qx, qy, ax, ay) / den
        ea, eb = tol / max(a.length, tol), tol / max(b.length, tol)
        if -ea <= ta <= 1 + ea and -eb <= tb <= 1 + eb:
            ta, tb = max(0.0, min(1.0, ta)), max(0.0, min(1.0, tb))
            pa = (a.x0 + ta * ax, a.y0 + ta * ay)
            pb = (b.x0 + tb * bx, b.y0 + tb * by)
            p = ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
            enda = ta <= ea or ta >= 1 - ea
            endb = tb <= eb or tb >= 1 - eb
            kind = ("endpoint_endpoint" if enda and endb else
                    "endpoint_on_segment" if enda or endb else "proper_crossing")
            return [(kind, p, ta, tb)]
        return []
    if abs(_cross(qx, qy, ax, ay)) > tol * max(a.length, 1.0):
        return []
    ll = ax * ax + ay * ay
    if ll == 0:
        return []
    t0 = (qx * ax + qy * ay) / ll
    t1 = t0 + (bx * ax + by * ay) / ll
    lo, hi = max(0.0, min(t0, t1)), min(1.0, max(t0, t1))
    if hi < lo - tol / max(a.length, tol):
        return []
    result = []
    for ta in (lo, hi):
        p = (a.x0 + ta * ax, a.y0 + ta * ay)
        tb = _point_on_segment(*p, b, tol)
        if tb is not None:
            result.append(("collinear_overlap", p, ta, tb))
    return result


def build_planar_graph(
    primitives: Iterable[Primitive],
    junction_dots: Iterable[tuple[float, float]] = (),
    tolerance: float = 0.05,
    dot_radius: float = 0.2,
) -> PlanarGraph:
    """Split linework and join only contacts with explicit CONNECTED decisions.

    Endpoint/endpoint contacts connect within tolerance. T contacts are UNKNOWN
    without a dot; X crossings are DISCONNECTED without a dot. Collinear overlap
    is split and connected while preserving both source primitive identities.
    Dot hits are projected onto each segment before node creation, so a slightly
    offset mark does not fail a coordinate-hash lookup.
    """
    if tolerance <= 0 or dot_radius < 0:
        raise ValueError("tolerance must be > 0 and dot_radius must be >= 0")
    segs, dots = list(primitives), list(junction_dots)
    cuts = [{0.0, 1.0} for _ in segs]
    contacts = []
    # Internal contact records retain segment indices and parametric positions;
    # no approximate midpoint/rounded-coordinate lookup is used for connectivity.
    joins = []

    for i, j in combinations(range(len(segs)), 2):
        a, b = segs[i], segs[j]
        for kind, p, ta, tb in _intersection(a, b, tolerance):
            cuts[i].add(ta)
            cuts[j].add(tb)
            if kind == "collinear_overlap":
                decision, reason = "CONNECTED", "shared_collinear_geometry"
            elif kind == "endpoint_endpoint":
                decision, reason = "CONNECTED", "endpoint_contact_within_tolerance"
            elif kind == "endpoint_on_segment":
                dot = any(math.hypot(p[0]-dx, p[1]-dy) <= dot_radius for dx, dy in dots)
                decision, reason = (("CONNECTED", "explicit_junction_dot") if dot else
                                    ("UNKNOWN", "endpoint_on_segment_without_dot"))
            else:
                dot = any(math.hypot(p[0]-dx, p[1]-dy) <= dot_radius for dx, dy in dots)
                decision, reason = (("CONNECTED", "explicit_junction_dot") if dot else
                                    ("DISCONNECTED", "proper_crossing_without_dot"))
            contacts.append(Contact(p[0], p[1], a.primitive_id, b.primitive_id,
                                    kind, decision, reason))
            if decision == "CONNECTED":
                joins.append((i, ta, j, tb, len(contacts)-1))

        # Tolerant endpoint contacts must also be found when the two supporting
        # infinite lines are parallel or slightly offset (no mathematical crossing).
        pair_has = lambda kind: any(
            c.primitive_a == a.primitive_id and c.primitive_b == b.primitive_id and c.kind == kind
            for c in contacts
        )
        endpoint_a = ((0.0, a.x0, a.y0), (1.0, a.x1, a.y1))
        endpoint_b = ((0.0, b.x0, b.y0), (1.0, b.x1, b.y1))
        if not pair_has("endpoint_endpoint"):
            for ta, ax, ay in endpoint_a:
                for tb, bx, by in endpoint_b:
                    if math.hypot(ax-bx, ay-by) <= tolerance:
                        cuts[i].add(ta)
                        cuts[j].add(tb)
                        contacts.append(Contact((ax+bx)/2, (ay+by)/2,
                            a.primitive_id, b.primitive_id, "endpoint_endpoint",
                            "CONNECTED", "endpoint_contact_within_tolerance"))
                        joins.append((i, ta, j, tb, len(contacts)-1))
                        break
                else:
                    continue
                break

        if not pair_has("endpoint_on_segment"):
            for ta, ax, ay in endpoint_a:
                tb = _point_on_segment(ax, ay, b, tolerance)
                if tb is not None and tolerance / max(b.length, tolerance) < tb < 1-tolerance / max(b.length, tolerance):
                    cuts[i].add(ta)
                    cuts[j].add(tb)
                    dot = any(math.hypot(ax-dx, ay-dy) <= dot_radius for dx, dy in dots)
                    decision, reason = (("CONNECTED", "explicit_junction_dot") if dot else
                                        ("UNKNOWN", "endpoint_on_segment_without_dot"))
                    contacts.append(Contact(ax, ay, a.primitive_id, b.primitive_id,
                        "endpoint_on_segment", decision, reason))
                    if decision == "CONNECTED":
                        joins.append((i, ta, j, tb, len(contacts)-1))
                    break
            else:
                for tb, bx, by in endpoint_b:
                    ta = _point_on_segment(bx, by, a, tolerance)
                    if ta is not None and tolerance / max(a.length, tolerance) < ta < 1-tolerance / max(a.length, tolerance):
                        cuts[i].add(ta)
                        cuts[j].add(tb)
                        dot = any(math.hypot(bx-dx, by-dy) <= dot_radius for dx, dy in dots)
                        decision, reason = (("CONNECTED", "explicit_junction_dot") if dot else
                                            ("UNKNOWN", "endpoint_on_segment_without_dot"))
                        contacts.append(Contact(bx, by, a.primitive_id, b.primitive_id,
                            "endpoint_on_segment", decision, reason))
                        if decision == "CONNECTED":
                            joins.append((i, ta, j, tb, len(contacts)-1))
                        break

    # A dot can be slightly offset from a wire. Project it, split there, then
    # connect all distinct primitive pairs hit by the same explicit dot.
    for dx, dy in dots:
        hits = []
        for i, seg in enumerate(segs):
            t = _point_on_segment(dx, dy, seg, dot_radius)
            if t is not None:
                cuts[i].add(t)
                hits.append((i, t))
        unique = {}
        for i, t in hits:
            unique.setdefault(segs[i].primitive_id, (i, t))
        values = list(unique.values())
        for (i, ti), (j, tj) in combinations(values, 2):
            contacts.append(Contact(dx, dy, segs[i].primitive_id, segs[j].primitive_id,
                                    "junction_dot", "CONNECTED", "explicit_junction_dot"))
            joins.append((i, ti, j, tj, len(contacts)-1))

    # Build segment-scoped cut nodes first. A node reference is (segment, t),
    # never a rounded x/y bucket; uncertain geometric coincidences stay separate.
    nodes, edges, cut_nodes = [], [], []
    for i, seg in enumerate(segs):
        ts = sorted(cuts[i])
        local = []
        for t in ts:
            nid = len(nodes)
            nodes.append(Node(nid, seg.x0 + t*(seg.x1-seg.x0),
                              seg.y0 + t*(seg.y1-seg.y0), {seg.primitive_id}))
            local.append((t, nid))
        cut_nodes.append(local)
        for (t0, u), (t1, v) in zip(local, local[1:]):
            if (t1-t0) * seg.length > 1e-9:
                edges.append(Edge(len(edges), u, v, {seg.primitive_id}))

    def resolve(i, t):
        return min(cut_nodes[i], key=lambda pair: abs(pair[0]-t))[1]

    parent = list(range(len(nodes)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, ta, j, tb, ci in joins:
        na, nb = resolve(i, ta), resolve(j, tb)
        union(na, nb)
        detail = contacts[ci]
        for nid in (na, nb):
            nodes[nid].decisions.append(
                f"{detail.decision}:{detail.kind}:{detail.primitive_a}:{detail.primitive_b}:{detail.reason}"
            )

    roots = sorted({find(i) for i in range(len(nodes))})
    remap = {root: k for k, root in enumerate(roots)}
    merged = []
    members = {}
    for i in range(len(nodes)):
        members.setdefault(find(i), []).append(i)
    for root in roots:
        group = members[root]
        n = Node(remap[root], nodes[group[0]].x, nodes[group[0]].y)
        for old in group:
            n.provenance.update(nodes[old].provenance)
            n.decisions.extend(nodes[old].decisions)
        merged.append(n)
    for edge in edges:
        edge.start, edge.end = remap[find(edge.start)], remap[find(edge.end)]
    return PlanarGraph(merged, edges, contacts, tolerance)
