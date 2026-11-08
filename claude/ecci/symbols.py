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


def _gap(a, b) -> float:
    dx = max(0.0, b[0] - a[2], a[0] - b[2])
    dy = max(0.0, b[1] - a[3], a[1] - b[3])
    return (dx * dx + dy * dy) ** 0.5


def _maxdim(b) -> float:
    return max(b[2] - b[0], b[3] - b[1])


def body_clusters(pi: PageIngest, cls: PageClassification, eps: float = EPS_DEFAULT,
                  gap_frac: float = 0.5) -> list[Cluster]:
    """Group BODY primitives into symbol-body clusters.

    Step 1: primitives that touch are one part. Step 2 (single link, repeated to a fixpoint):
    two parts merge when the gap between their bounding boxes is <= gap_frac x the LARGER
    DIMENSION OF THE SMALLER PART. Overlap and containment are gap 0 (circle + inner arc). The
    smaller part decides so that a big body never swallows a distant small thing, and two
    neighbouring symbols stay apart because the wires between them are not BODY. gap_frac=0
    merges only touching or overlapping parts. Parts farther apart than the rule allows stay
    separate (a known limit, see docs)."""
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

    if gap_frac >= 0:
        for _ in range(16):                       # fixpoint; merged parts grow, so iterate
            groups: dict[int, list[int]] = {}
            for i in prims:
                groups.setdefault(find(i), []).append(i)
            boxes = {}
            for root, ids in groups.items():
                bs = [_bbox(prims[i]) for i in ids]
                boxes[root] = (min(b[0] for b in bs), min(b[1] for b in bs),
                               max(b[2] for b in bs), max(b[3] for b in bs))
            order = sorted(boxes, key=lambda r: boxes[r][0])
            changed = False
            for ai, a in enumerate(order):
                ba = boxes[a]
                reach = ba[2] + gap_frac * _maxdim(ba)
                for b in order[ai + 1:]:
                    bb = boxes[b]
                    if bb[0] > reach:             # sorted by x0: no later part can be within reach
                        break
                    if find(a) != find(b) and _gap(ba, bb) <= gap_frac * min(_maxdim(ba), _maxdim(bb)):
                        ra, rb = find(a), find(b)
                        parent[max(ra, rb)] = min(ra, rb)
                        changed = True
            if not changed:
                break

    groups = {}
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
