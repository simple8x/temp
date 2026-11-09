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


def _geom_key(p) -> tuple:
    pts = tuple((round(x, 2), round(y, 2)) for x, y in p.pts)
    return min(pts, pts[::-1])


def find_stubs(prims: dict, eps: float = EPS_DEFAULT, max_len: float = float("inf"),
               min_host: float = 0.0, axis_tol: float = 0.01) -> set[int]:
    """Ids of dangling stubs among `prims`: 2-vertex open primitives with exactly one end touching another
    primitive of size >= min_host and the other end free (pin lines drawn in the body colour). Axis-aligned only
    (ASSUMPTION: pin lines are horizontal or vertical; diagonal LED arrows and the like are body parts). Pure geometry."""
    from .flatten import point_segment_distance
    out: set[int] = set()
    items = list(prims.items())
    gk = {i: _geom_key(q) for i, q in items}          # once per primitive, not once per candidate pair
    cell = 20.0
    grid: dict[tuple[int, int], list[int]] = {}
    for i, q in items:
        bq = _bbox(q)
        for cx in range(int(bq[0] // cell), int(bq[2] // cell) + 1):
            for cy in range(int(bq[1] // cell), int(bq[3] // cell) + 1):
                grid.setdefault((cx, cy), []).append(i)

    def nbrs(i: int, bb) -> list[int]:
        got: set[int] = set()
        for cx in range(int((bb[0] - 2) // cell), int((bb[2] + 2) // cell) + 1):
            for cy in range(int((bb[1] - 2) // cell), int((bb[3] + 2) // cell) + 1):
                got.update(grid.get((cx, cy), ()))
        got.discard(i)
        return [j for j in sorted(got) if _gap(bb, _bbox(prims[j])) <= eps + 0.5 * (prims[i].stroke_w + prims[j].stroke_w)]

    def touching(j: int) -> list[int]:
        qj = prims[j]
        res_ = []
        for k in nbrs(j, _bbox(qj)):
            qk = prims[k]
            tol = eps + 0.5 * (qj.stroke_w + qk.stroke_w)
            if any(point_segment_distance(v, s0, s1) <= tol for v in qj.pts for _, s0, s1 in qk.segments()) or \
               any(point_segment_distance(v, s0, s1) <= tol for v in qk.pts for _, s0, s1 in qj.segments()):
                res_.append(k)
        return res_

    def dangling(i: int, far: list[int], near: list[int], cap: int = 60) -> bool:
        """True when the primitives touched at the far end form a group of <= cap primitives that never reaches
        anything touched at the near end except through primitive i: i is a bridge between the body and that group."""
        seen = set(far)
        stack = list(far)
        near_set = set(near)
        while stack:
            j = stack.pop()
            if j in near_set:
                return False
            for k in touching(j):
                if k != i and k not in seen:
                    seen.add(k)
                    if len(seen) > cap:
                        return False
                    stack.append(k)
        return not (seen & near_set)

    for i, p in items:
        if len(p.pts) != 2 or p.line_closed:
            continue
        a, b = p.pts[0], p.pts[-1]
        if ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 > max_len:
            continue
        if min(abs(a[0] - b[0]), abs(a[1] - b[1])) > axis_tol:
            continue
        bb = _bbox(p)
        tol_i = eps + 0.5 * p.stroke_w
        touched = ([], [])
        for j in nbrs(i, bb):
            q = prims[j]
            if gk[j] == gk[i] or _maxdim(_bbox(q)) < min_host:
                continue
            tol = eps + 0.5 * (p.stroke_w + q.stroke_w)
            for k, end in enumerate((a, b)):
                if any(point_segment_distance(end, s0, s1) <= tol for _, s0, s1 in q.segments()):
                    touched[k].append(j)
        t0, t1 = bool(touched[0]), bool(touched[1])
        if t0 != t1:
            out.add(i)
        elif t0 and t1 and (dangling(i, touched[1], touched[0]) or dangling(i, touched[0], touched[1])):
            out.add(i)          # one end carries a hanging group (a power symbol on the pin tip), not the body
    return out


def body_clusters(pi: PageIngest, cls: PageClassification, eps: float = EPS_DEFAULT,
                  gap_frac: float = 0.5, exclude: frozenset = frozenset(),
                  min_gap_part_frac: float = 0.003, stub_aware: bool = True, bare_gap_frac: float | None = None) -> list[Cluster]:
    """Group BODY primitives into symbol-body clusters.

    Step 1: primitives that touch are one part. Step 2 (single link, repeated to a fixpoint):
    two parts merge when the gap between their bounding boxes is <= gap_frac x the LARGER
    DIMENSION OF THE SMALLER PART. Overlap and containment are gap 0 (circle + inner arc). The
    smaller part decides so that a big body never swallows a distant small thing, and two
    neighbouring symbols stay apart because the wires between them are not BODY. gap_frac=0
    merges only touching or overlapping parts. Parts farther apart than the rule allows stay
    separate (a known limit, see docs)."""
    if bare_gap_frac is None:                    # gap merging switched off (0) stays off for plates too
        bare_gap_frac = max(gap_frac, 0.5) if gap_frac > 0 else 0.0
    prims = {p.id: p for p in pi.prims if cls.records[p.id].cls == LC_BODY and p.kind == "polyline"
             and p.id not in exclude}
    if not prims:
        return []
    all_prims = prims
    min_part = min_gap_part_frac * (pi.geom.width ** 2 + pi.geom.height ** 2) ** 0.5
    stubs = find_stubs(all_prims, eps, min_host=min_part) if stub_aware else set()
    prims = {i: p for i, p in all_prims.items() if i not in stubs} or all_prims
    if prims is all_prims:
        stubs = set()
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

    # parts smaller than this (glyph strokes, tick marks) never merge by proximity: only by touching.
    # Without it, text strokes chain into one page-sized "cluster" that matches nothing.
    min_part = min_gap_part_frac * (pi.geom.width ** 2 + pi.geom.height ** 2) ** 0.5
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
            # a part that is a single bare 2-vertex line (pin line, plate, glyph stroke) merges by proximity only
            # with another bare line (capacitor plates), never with a larger part. Otherwise rows of pins/strokes chain a whole sheet together.
            bare = {r for r, ids in groups.items()
                    if all(len(prims[i].pts) == 2 and not prims[i].line_closed for i in ids)
                    and len({_geom_key(prims[i]) for i in ids}) == 1}      # a line, possibly plotted twice
            order = sorted(boxes, key=lambda r: boxes[r][0])
            changed = False
            for ai, a in enumerate(order):
                ba = boxes[a]
                reach = ba[2] + max(gap_frac, bare_gap_frac) * _maxdim(ba)
                if _maxdim(ba) < min_part:
                    continue
                for b in order[ai + 1:]:
                    bb = boxes[b]
                    if _maxdim(bb) < min_part:
                        continue
                    if bb[0] > reach:             # sorted by x0: no later part can be within reach
                        break
                    if (a in bare) != (b in bare) and _gap(ba, bb) > 0:
                        continue                  # a bare line joins a larger part only when inside/overlapping it
                    gf = bare_gap_frac if (a in bare and b in bare) else gap_frac   # plates sit far apart relative to size
                    if find(a) != find(b) and _gap(ba, bb) <= gf * min(_maxdim(ba), _maxdim(bb)):
                        ra, rb = find(a), find(b)
                        parent[max(ra, rb)] = min(ra, rb)
                        changed = True
            if not changed:
                break

    groups = {}
    for i in prims:
        groups.setdefault(find(i), []).append(i)
    if stubs:                                  # a stub joins the cluster of the body part it touches
        from .flatten import point_segment_distance
        owner = {i: r for r, ids in groups.items() for i in ids}
        for sid in sorted(stubs):
            sp = all_prims[sid]
            hit, hit_size = None, -1.0
            for i in sorted(prims):
                if any(point_segment_distance(e, s0, s1) <= eps + 0.5 * (sp.stroke_w + prims[i].stroke_w) for e in (sp.pts[0], sp.pts[-1])
                       for _, s0, s1 in prims[i].segments()):
                    size = sum(_maxdim(_bbox(all_prims[k])) for k in groups[owner[i]])   # prefer the body over a hanging symbol
                    if size > hit_size:
                        hit, hit_size = owner[i], size
            if hit is None:
                groups[("stub", sid)] = [sid]
            else:
                groups[hit].append(sid)
        prims = all_prims
    out: list[Cluster] = []
    for cid, key in enumerate(sorted(groups, key=lambda k: min(groups[k]))):
        ids = tuple(sorted(groups[key]))
        verts = [q for i in ids for q in prims[i].pts]
        segs = [(a, b) for i in ids for _, a, b in prims[i].segments()]
        xs, ys = [v[0] for v in verts], [v[1] for v in verts]
        out.append(Cluster(cid, ids, (min(xs), min(ys), max(xs), max(ys)), tuple(verts), tuple(segs)))
    return out


def _rebuild(cid: int, ids: tuple[int, ...], by_id: dict) -> Cluster:
    verts = [q for i in ids for q in by_id[i].pts]
    segs = [(a, b) for i in ids for _, a, b in by_id[i].segments()]
    xs, ys = [v[0] for v in verts], [v[1] for v in verts]
    return Cluster(cid, ids, (min(xs), min(ys), max(xs), max(ys)), tuple(verts), tuple(segs))


def strip_stubs(cl: Cluster, by_id: dict, eps: float = EPS_DEFAULT, max_frac: float = 1.5):
    """Split off dangling stubs: 2-vertex primitives with exactly one end touching the rest of the cluster and
    the other end free. In some plotters (KiCad) pin lines are drawn in the body colour and land in the cluster;
    the template model has no pin lines, so matching is retried on the cluster WITHOUT them.

    Returns (reduced cluster, stub prim ids) or None when nothing can be stripped. Pure geometry: the stubs
    are NOT assumed to be pins here; the template decides, and the stub primitives then serve as the measured
    second evidence (their lineage never contains the library token)."""
    from .flatten import point_segment_distance
    ids = list(cl.prim_ids)
    maxd = _maxdim(cl.bbox)
    stubs: list[int] = []
    for i in ids:
        p = by_id[i]
        if len(p.pts) != 2 or p.line_closed:
            continue
        a, b = p.pts[0], p.pts[-1]
        if min(abs(a[0] - b[0]), abs(a[1] - b[1])) > 0.01:
            continue
        if ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 > max_frac * maxd:
            continue
        touch = []
        for end in (a, b):
            touch.append(any(point_segment_distance(end, s0, s1) <= eps + 0.5 * (p.stroke_w + by_id[j].stroke_w)
                             for j in ids if j != i and _geom_key(by_id[j]) != _geom_key(p)
                             for _, s0, s1 in by_id[j].segments()))
        if touch[0] != touch[1]:
            stubs.append(i)
    rest = tuple(i for i in ids if i not in set(stubs))
    if not stubs or not rest:
        return None
    return _rebuild(cl.id, rest, by_id), tuple(stubs)
