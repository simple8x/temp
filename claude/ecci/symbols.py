"""Body clusters: groups of BODY primitives that touch (geometry only, wires excluded)."""

from __future__ import annotations

from dataclasses import dataclass

from .arrangement import EPS_DEFAULT, build_arrangement, independent_prims_at
from .classify import LC_BODY, PageClassification
from .ir import PageIngest


@dataclass(frozen=True)
class Cluster:
    id: int
    prim_ids: tuple[int, ...]
    bbox: tuple[float, float, float, float]
    verts: tuple[tuple[float, float], ...]
    segs: tuple[tuple[tuple[float, float], tuple[float, float]], ...]


def _bbox(p) -> tuple[float, float, float, float]:
    xs = [q[0] for q in p.pts]
    ys = [q[1] for q in p.pts]
    return min(xs), min(ys), max(xs), max(ys)


def body_clusters(pi: PageIngest, cls: PageClassification, eps: float = EPS_DEFAULT,
                  merge_gap: float = 0.0) -> list[Cluster]:
    """Group BODY prims that touch, or whose bounding boxes overlap/contain each other
    (circle + inner arc), or are within `merge_gap` (default 0). Parts of one symbol that
    neither touch nor overlap (e.g. capacitor plates) stay separate clusters: a known limit."""
    prims = {p.id: p for p in pi.prims if cls.records[p.id].cls == LC_BODY and p.kind == "polyline"}
    if not prims:
        return []
    graph = build_arrangement(list(prims.values()), eps)
    parent = {i: i for i in prims}

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for node in graph.nodes:
        ids = sorted(independent_prims_at(graph, node.id))
        for other in ids[1:]:
            ra, rb = find(ids[0]), find(other)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    boxes = sorted(((_bbox(prims[i]), i) for i in prims), key=lambda t: t[0][0])
    active: list[tuple[tuple[float, float, float, float], int]] = []
    for box, i in boxes:
        active = [(b, j) for b, j in active if b[2] + merge_gap >= box[0]]
        for b, j in active:
            if b[1] - merge_gap <= box[3] and box[1] - merge_gap <= b[3]:
                ra, rb = find(i), find(j)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
        active.append((box, i))
    groups: dict[int, list[int]] = {}
    for i in prims:
        groups.setdefault(find(i), []).append(i)
    out: list[Cluster] = []
    for cid, key in enumerate(sorted(groups, key=lambda k: min(groups[k]))):
        ids = tuple(sorted(groups[key]))
        verts = [q for i in ids for q in prims[i].pts]
        segs = [(a, b) for i in ids for _, a, b in prims[i].segments()]
        xs, ys = [v[0] for v in verts], [v[1] for v in verts]
        out.append(Cluster(cid, ids, (min(xs), min(ys), max(xs), max(ys)), tuple(verts), tuple(segs)))
    return out
