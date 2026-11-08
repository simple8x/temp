"""Path-grouped PDF stroke-role diagnostics.

This is deliberately conservative: it identifies closed path structure and
keeps every unreviewed stroke UNKNOWN. It never calls a stroke a wire merely
because it touches another stroke.
"""
from __future__ import annotations
from collections import defaultdict
import math


def _near(a, b, tol):
    return math.hypot(a[0]-b[0], a[1]-b[1]) <= tol


def _closed_chain(edges, tol):
    # Endpoint graph: a single closed path has degree 2 at every vertex.
    vertices = []
    degree = []
    def vertex(p):
        for i, q in enumerate(vertices):
            if _near(p, q, tol):
                return i
        vertices.append(p)
        degree.append(0)
        return len(vertices)-1
    for a, b in edges:
        ia, ib = vertex(a), vertex(b)
        if ia == ib:
            return False
        degree[ia] += 1
        degree[ib] += 1
    return len(vertices) >= 3 and all(d == 2 for d in degree)


def _axis_aligned_rectangle(edges, tol):
    if len(edges) != 4:
        return False
    points = [p for edge in edges for p in edge]
    xs = sorted({round(p[0] / tol) for p in points})
    ys = sorted({round(p[1] / tol) for p in points})
    if len(xs) != 2 or len(ys) != 2:
        return False
    for a, b in edges:
        if abs(a[0]-b[0]) > tol and abs(a[1]-b[1]) > tol:
            return False
    return True


def classify_path_groups(primitives, tolerance_pt=0.35):
    groups = defaultdict(list)
    for p in primitives:
        groups[p.get("path_index", -1)].append(p)
    rows = []
    counts = defaultdict(int)
    for path_id, members in sorted(groups.items(), key=lambda x: x[0]):
        edges = [(m["a"], m["b"]) for m in members]
        closed = _closed_chain(edges, tolerance_pt)
        rectangle = closed and _axis_aligned_rectangle(edges, tolerance_pt)
        if rectangle:
            role = "CLOSED_RECTANGULAR_PATH_CANDIDATE"
        elif closed:
            role = "CLOSED_NONRECTANGULAR_PATH_CANDIDATE"
        elif len(members) > 1:
            role = "MULTI_EDGE_OPEN_PATH_AMBIGUOUS"
        else:
            role = "SINGLE_STROKE_AMBIGUOUS"
        counts[role] += len(members)
        rows.append({
            "path_index": path_id,
            "primitive_ids": [m["id"] for m in members],
            "edge_count": len(members),
            "closed_path_candidate": closed,
            "rectangular_path_candidate": rectangle,
            "role_candidate": role,
            "authority": "CANDIDATE_ONLY",
        })
    return {
        "algorithm": "path_grouped_closed_chain_classifier_v1",
        "path_count": len(rows),
        "primitive_count": len(primitives),
        "primitive_role_counts": dict(sorted(counts.items())),
        "paths": rows,
        "wire_classification": "NOT_ATTEMPTED",
        "ground_truth_status": "NOT_AUTHORED",
        "electrical_authority": "NONE",
        "limitations": [
            "PDF path grouping is a drawing-structure clue, not semantic object identity.",
            "Closed rectangles may be symbol bodies, frames, or graphics.",
            "Open multi-edge paths may be wires, symbols, text, or graphics.",
            "No path is promoted to WIRE, PIN, JUNCTION, or VERIFIED connectivity.",
        ],
    }
