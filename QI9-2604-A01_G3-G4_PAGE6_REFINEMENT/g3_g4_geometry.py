"""Evidence-gated geometry primitives for schematic G3/G4."""
from dataclasses import dataclass, asdict
from collections import defaultdict
from math import hypot, floor


@dataclass(frozen=True)
class Segment:
    id: str
    page: int
    a: tuple
    b: tuple
    role: str = "UNKNOWN"
    role_evidence: str = "NONE"
    path_id: str = None


@dataclass(frozen=True)
class JunctionDot:
    page: int
    center: tuple
    radius: float
    evidence_id: str
    verified: bool = False


@dataclass(frozen=True)
class PinAnchor:
    reference: str
    pin_number: str
    point: tuple
    template_id: str
    template_verified: bool
    evidence_id: str


@dataclass(frozen=True)
class LabelAnchor:
    name: str
    point: tuple
    evidence_id: str
    attachment_verified: bool = False


def dist(a, b):
    return hypot(a[0] - b[0], a[1] - b[1])


def cross(a, b, c):
    return ((b[0] - a[0]) * (c[1] - a[1])
            - (b[1] - a[1]) * (c[0] - a[0]))


def onseg(p, a, b, t):
    return (abs(cross(a, b, p)) <= t * max(1., dist(a, b))
            and min(a[0], b[0]) - t <= p[0] <= max(a[0], b[0]) + t
            and min(a[1], b[1]) - t <= p[1] <= max(a[1], b[1]) + t)


def relation(s1, s2, tol):
    a, b, c, d = s1.a, s1.b, s2.a, s2.b
    if dist(a, b) <= tol or dist(c, d) <= tol:
        return "DEGENERATE", None
    o1, o2 = cross(a, b, c), cross(a, b, d)
    o3, o4 = cross(c, d, a), cross(c, d, b)
    if all(abs(v) <= tol * max(1., dist(a, b), dist(c, d))
           for v in (o1, o2, o3, o4)):
        axis = 0 if abs(b[0] - a[0]) >= abs(b[1] - a[1]) else 1
        lo = max(min(a[axis], b[axis]), min(c[axis], d[axis]))
        hi = min(max(a[axis], b[axis]), max(c[axis], d[axis]))
        if hi < lo - tol:
            return "DISJOINT", None
        if hi - lo > tol:
            return "OVERLAP", None
        pts = [p for p in (a, b, c, d)
               if onseg(p, a, b, tol) and onseg(p, c, d, tol)]
        return ("ENDPOINT_ENDPOINT", pts[0]) if pts else ("DISJOINT", None)
    if o1 * o2 < -tol * tol and o3 * o4 < -tol * tol:
        den = ((a[0] - b[0]) * (c[1] - d[1])
               - (a[1] - b[1]) * (c[0] - d[0]))
        det1 = a[0] * b[1] - a[1] * b[0]
        det2 = c[0] * d[1] - c[1] * d[0]
        p = ((det1 * (c[0] - d[0]) - (a[0] - b[0]) * det2) / den,
             (det1 * (c[1] - d[1]) - (a[1] - b[1]) * det2) / den)
        return "CROSSING", p
    pts = [p for p, x, y in ((a, c, d), (b, c, d),
                              (c, a, b), (d, a, b))
           if onseg(p, x, y, tol)]
    if not pts:
        return "DISJOINT", None
    p = pts[0]
    e1 = min(dist(p, a), dist(p, b)) <= tol
    e2 = min(dist(p, c), dist(p, d)) <= tol
    return ("ENDPOINT_ENDPOINT" if e1 and e2 else "T"), p


def candidate_pairs(segments, cell_size=4.0, tolerance=1e-4,
                    max_cells_per_segment=4096):
    """Return a complete superset of page-local pairs with overlapping AABBs.

    The uniform-grid broad phase prunes impossible pairs only. Very long
    bounding boxes use a page-local fallback to preserve completeness without
    allocating excessive grid cells. No electrical semantics are inferred.
    """
    if cell_size <= 0:
        raise ValueError("cell_size must be positive")
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative")

    buckets = defaultdict(list)
    long_segments = defaultdict(list)
    page_indices = defaultdict(list)
    for index, segment in enumerate(segments):
        page_indices[segment.page].append(index)
        ax, ay = segment.a
        bx, by = segment.b
        x0 = floor((min(ax, bx) - tolerance) / cell_size)
        x1 = floor((max(ax, bx) + tolerance) / cell_size)
        y0 = floor((min(ay, by) - tolerance) / cell_size)
        y1 = floor((max(ay, by) + tolerance) / cell_size)
        cell_count = (x1 - x0 + 1) * (y1 - y0 + 1)
        if cell_count > max_cells_per_segment:
            long_segments[segment.page].append(index)
            continue
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                buckets[(segment.page, gx, gy)].append(index)

    pairs = set()
    for indices in buckets.values():
        unique = sorted(set(indices))
        for offset, left in enumerate(unique):
            for right in unique[offset + 1:]:
                pairs.add((left, right))

    for page, long_indices in long_segments.items():
        for left in long_indices:
            for right in page_indices[page]:
                if left != right:
                    pairs.add((min(left, right), max(left, right)))
    return sorted(pairs)


def analyze_connectivity(segments, dots=(), tol=1e-4, cell_size=4.0):
    segs = list(segments)
    dots_by_page = defaultdict(list)
    for dot in dots:
        if dot.verified and dot.evidence_id:
            dots_by_page[dot.page].append(dot)

    pairs = candidate_pairs(segs, cell_size=cell_size, tolerance=tol)
    candidates, joins = [], []
    for i, j in pairs:
        s1, s2 = segs[i], segs[j]
        kind, point = relation(s1, s2, tol)
        if kind == "DISJOINT":
            continue
        rec = {
            "page": s1.page,
            "segment_ids": [s1.id, s2.id],
            "relation": kind,
            "point": list(point) if point else None,
            "electrical_status": "UNKNOWN",
            "evidence_ids": [],
        }
        candidates.append(rec)
        roles = all(
            s.role == "WIRE"
            and s.role_evidence in ("SOURCE_REVIEWED", "VALIDATED_LAYER_RULE")
            for s in (s1, s2)
        )
        continuity = bool(
            s1.path_id and s1.path_id == s2.path_id
            and kind == "ENDPOINT_ENDPOINT"
        )
        dot = next((
            d for d in dots_by_page[s1.page]
            if point and dist(d.center, point) <= d.radius + tol
        ), None)
        if roles and (continuity or (
            dot and kind in ("T", "CROSSING", "ENDPOINT_ENDPOINT")
        )):
            rec["electrical_status"] = "VERIFIED_JOIN"
            if dot:
                rec["evidence_ids"].append(dot.evidence_id)
            if continuity:
                rec["evidence_ids"].append("PDF_PATH_CONTINUITY:" + s1.path_id)
            joins.append(dict(rec))

    return {
        "schema": "qi9-g4-geometry-evidence/v3",
        "segment_count": len(segs),
        "broadphase_pair_count": len(pairs),
        "candidate_count": len(candidates),
        "verified_join_count": len(joins),
        "candidates": candidates,
        "verified_joins": joins,
        "policy": {
            "geometry_is_not_connectivity": True,
            "spatial_index_is_candidate_pruning_only": True,
            "unknown_is_fail_closed": True,
        },
    }


def validate_pin_anchor(x):
    ok = bool(x.reference and x.pin_number and x.template_id
              and x.template_verified and x.evidence_id)
    return {**asdict(x), "status": "VERIFIED_ANCHOR" if ok else "UNKNOWN"}


def validate_label_attachment(label, segment):
    ok = (
        label.attachment_verified and bool(label.evidence_id)
        and segment.role == "WIRE"
        and segment.role_evidence in ("SOURCE_REVIEWED", "VALIDATED_LAYER_RULE")
        and onseg(label.point, segment.a, segment.b, 1e-4)
    )
    return {
        "label": label.name,
        "segment_id": segment.id,
        "status": "VERIFIED_ATTACHMENT" if ok else "UNKNOWN",
        "evidence_id": label.evidence_id if ok else None,
    }
