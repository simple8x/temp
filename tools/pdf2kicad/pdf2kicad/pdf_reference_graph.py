"""Independent PDF vector-geometry graph for cross-checking parser topology.

This module intentionally does not import pdf2kicad.topology, graph_netlist,
netlist, or connectivity_evidence. It reads PDF drawing paths directly.
It emits geometric connectivity candidates, NOT an authoritative electrical
netlist: symbol-body strokes and non-wire artwork still require classification.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
import pymupdf


def _point_segment_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    den = dx * dx + dy * dy
    t = 0.0 if den == 0 else max(0.0, min(1.0, ((p[0]-a[0])*dx+(p[1]-a[1])*dy)/den))
    q = (a[0] + t*dx, a[1] + t*dy)
    return math.hypot(p[0]-q[0], p[1]-q[1])


class _UF:
    def __init__(self, n): self.p = list(range(n))
    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x
    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b: self.p[b] = a


def build_pdf_reference_graph(pdf_path: str, page_number: int, *, rotate: int = 0,
                              tolerance: float = 0.35, junction_radius: float = 1.0):
    """Read raw PDF paths and build a conservative H/V geometric graph.

    Endpoint-to-endpoint and collinear endpoint contacts connect. An endpoint
    landing on another segment connects only with an explicit filled-dot
    candidate. Interior crossings connect only when a filled dot is present.
    """
    if page_number < 1: raise ValueError("page_number is 1-based")
    doc = pymupdf.open(pdf_path)
    if page_number > len(doc): raise ValueError("page out of range")
    page = doc[page_number - 1]
    mb = page.mediabox
    W, H = mb.width, mb.height

    def xy(p):
        x, y = p.x - mb.x0, p.y - mb.y0
        if rotate == 90: x, y = H-y, x
        elif rotate == 180: x, y = W-x, H-y
        elif rotate == 270: x, y = y, W-x
        return (round(x, 4), round(y, 4))

    segments, dots = [], []
    for path_index, drawing in enumerate(page.get_drawings()):
        rect = drawing.get("rect")
        fill = drawing.get("fill")
        if fill is not None and rect is not None and max(rect.width, rect.height) <= 3.0 and min(rect.width, rect.height) >= .6:
            dots.append(xy(pymupdf.Point((rect.x0+rect.x1)/2, (rect.y0+rect.y1)/2)))
        for item_index, item in enumerate(drawing.get("items", [])):
            if item[0] == "l":
                a, b = xy(item[1]), xy(item[2])
                if math.hypot(a[0]-b[0], a[1]-b[1]) > 1e-5:
                    dx, dy = abs(a[0]-b[0]), abs(a[1]-b[1])
                    if min(dx, dy) <= tolerance:
                        segments.append({"id": len(segments), "path": path_index, "item": item_index,
                                         "a": a, "b": b, "axis": "h" if dy <= tolerance else "v"})
            elif item[0] == "re":
                r = item[1]
                pts = [pymupdf.Point(r.x0,r.y0),pymupdf.Point(r.x1,r.y0),
                       pymupdf.Point(r.x1,r.y1),pymupdf.Point(r.x0,r.y1)]
                for a0,b0 in zip(pts,pts[1:]+pts[:1]):
                    a,b=xy(a0),xy(b0)
                    segments.append({"id":len(segments),"path":path_index,"item":item_index,
                                     "a":a,"b":b,"axis":"h" if abs(a[1]-b[1])<=tolerance else "v"
                                     if abs(a[0]-b[0])<=tolerance else "other"})
    # Candidate electrical strokes are only axis-aligned raw path segments.
    segments = [s for s in segments if s["axis"] in ("h","v")]
    # Filtering can remove oblique rectangle edges whose pre-filter IDs leave gaps.
    # Re-index the retained segments before using IDs as union-find array offsets.
    for segment_id, segment in enumerate(segments):
        segment["id"] = segment_id
    uf = _UF(len(segments))
    from bisect import bisect_left, bisect_right

    # Spatial hash for endpoint contacts: avoids the former O(E^2) pair scan.
    cell_size = max(tolerance, 1e-6)
    endpoint_cells = defaultdict(list)
    def cell_of(p, size):
        return (math.floor(p[0] / size), math.floor(p[1] / size))
    endpoints = [(i, p) for i, s in enumerate(segments) for p in (s["a"], s["b"])]
    for i, p in endpoints:
        cx, cy = cell_of(p, cell_size)
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for j, q in endpoint_cells[(gx, gy)]:
                    if math.hypot(p[0] - q[0], p[1] - q[1]) <= tolerance:
                        uf.union(i, j)
        endpoint_cells[(cx, cy)].append((i, p))

    # Junction-dot lookup is also spatially indexed.
    dot_cells = defaultdict(list)
    dot_size = max(junction_radius, 1e-6)
    for dot in dots:
        dot_cells[cell_of(dot, dot_size)].append(dot)
    def dot_near(p):
        cx, cy = cell_of(p, dot_size)
        return any(
            math.hypot(p[0] - d[0], p[1] - d[1]) <= junction_radius
            for gx in (cx - 1, cx, cx + 1)
            for gy in (cy - 1, cy, cy + 1)
            for d in dot_cells[(gx, gy)]
        )

    # Sweep candidate verticals by x-range for each horizontal segment.
    # Work is proportional to actual x-overlap candidates, not all segment pairs.
    vertical_by_x = defaultdict(list)
    for segment in segments:
        if segment["axis"] == "v":
            x = (segment["a"][0] + segment["b"][0]) / 2
            y0, y1 = sorted((segment["a"][1], segment["b"][1]))
            vertical_by_x[x].append((y0, y1, segment))
    vertical_xs = sorted(vertical_by_x)
    intersections = []
    for h in (s for s in segments if s["axis"] == "h"):
        hy = (h["a"][1] + h["b"][1]) / 2
        hx0, hx1 = sorted((h["a"][0], h["b"][0]))
        left = bisect_left(vertical_xs, hx0 - tolerance)
        right = bisect_right(vertical_xs, hx1 + tolerance)
        for vx in vertical_xs[left:right]:
            for vy0, vy1, v in vertical_by_x[vx]:
                if not (vy0 - tolerance <= hy <= vy1 + tolerance):
                    continue
                p = (vx, hy)
                endpoint_hit = any(
                    math.hypot(p[0] - e[0], p[1] - e[1]) <= tolerance
                    for e in (h["a"], h["b"], v["a"], v["b"])
                )
                marked = dot_near(p)
                if marked:
                    uf.union(h["id"], v["id"])
                intersections.append({
                    "point": p,
                    "segments": [h["id"], v["id"]],
                    "explicit_dot_candidate": marked,
                    "endpoint_contact": endpoint_hit,
                    "decision": "JOIN" if marked else "CROSSING_OR_T_CONTACT_UNRESOLVED",
                })
    groups=defaultdict(list)
    for s in segments: groups[uf.find(s["id"])].append(s["id"])
    components=[sorted(v) for _,v in sorted(groups.items(),key=lambda kv:min(kv[1]))]
    text_words=[]
    for w in page.get_text("words"):
        a,b=xy(pymupdf.Point(w[0],w[1])),xy(pymupdf.Point(w[2],w[3]))
        text_words.append({"box":[a[0],a[1],b[0],b[1]],"text":w[4]})
    payload={"schema":"pdf-reference-geometry/v1","page":page_number,
             "source_sha256":hashlib.sha256(open(pdf_path,"rb").read()).hexdigest(),
             "coordinate_units":"PDF points","segments":segments,"dots":dots,
             "components":components,"intersections":intersections,"text":text_words,
             "scope":"INDEPENDENT_GEOMETRIC_CANDIDATE_NOT_ELECTRICAL_NETLIST",
             "limitations":["symbol strokes are not fully classified as wires",
                            "filled-dot candidates may include non-junction artwork",
                            "pin identity and symbol ownership are not inferred"]}
    return payload


def compare_parser_memberships(reference_graph: dict, parser_evidence: dict,
                               *, tolerance: float = 0.5):
    """Match parser terminal coordinates to raw-PDF graph components; report exceptions.

    This is a diagnostic gate only. A geometric component match is not proof
    that the corresponding stroke is an electrical wire.
    """
    segs=reference_graph["segments"]
    comp_of={sid:ci for ci,ids in enumerate(reference_graph["components"]) for sid in ids}
    def nearest_component(x,y):
        best=(float("inf"),None)
        for s in segs:
            d=_point_segment_distance((x,y),s["a"],s["b"])
            if d<best[0]: best=(d,comp_of[s["id"]])
        return best
    rows=[]
    page_number = reference_graph.get("page")
    for row in parser_evidence.get("memberships",[]):
        t=row.get("terminal",{})
        d,c=nearest_component(t.get("x",0),t.get("y",0))
        rows.append({"ref":row.get("ref"),"pin":row.get("pin_identity"),"net":row.get("net"),
                     "terminal_index":row.get("terminal_index"),"pdf_component":c,
                     "distance_pt":round(d,4),"status":"GEOMETRY_MATCH_CANDIDATE" if d<=tolerance
                     else "NO_INDEPENDENT_GEOMETRY_MATCH",
                     "electrical_verification":"NOT_ESTABLISHED"})
    comparable = [r for r in rows if r["status"] == "GEOMETRY_MATCH_CANDIDATE"
                  and r.get("net") and r.get("pdf_component") is not None]
    pair_comparisons = agreement_pairs = mismatch_pairs = 0
    parser_same_net_pairs = graph_same_component_pairs = 0
    mismatch_examples = []
    for index, left in enumerate(comparable):
        for right in comparable[index + 1:]:
            parser_same = left["net"] == right["net"]
            graph_same = left["pdf_component"] == right["pdf_component"]
            pair_comparisons += 1
            parser_same_net_pairs += int(parser_same)
            graph_same_component_pairs += int(graph_same)
            if parser_same == graph_same:
                agreement_pairs += 1
            else:
                mismatch_pairs += 1
                if len(mismatch_examples) < 50:
                    mismatch_examples.append({
                        "left": {"ref": left.get("ref"), "pin": left.get("pin"),
                                 "parser_net": left.get("net"), "pdf_component": left.get("pdf_component")},
                        "right": {"ref": right.get("ref"), "pin": right.get("pin"),
                                  "parser_net": right.get("net"), "pdf_component": right.get("pdf_component")},
                        "decision": "PARTITION_DISAGREEMENT_CANDIDATE",
                    })
    return {"schema":"pdf-graph-comparison/v1","parser_rows":len(rows),
            "geometry_candidates":sum(r["status"]=="GEOMETRY_MATCH_CANDIDATE" for r in rows),
            "exceptions":sum(r["status"]!="GEOMETRY_MATCH_CANDIDATE" for r in rows),
            "partition_comparison":{
                "comparable_terminal_rows":len(comparable),
                "pair_comparisons":pair_comparisons,
                "agreement_pairs":agreement_pairs,
                "mismatch_pairs":mismatch_pairs,
                "parser_same_net_pairs":parser_same_net_pairs,
                "independent_graph_same_component_pairs":graph_same_component_pairs,
                "status":"PAIRWISE_PARTITION_MATCH" if mismatch_pairs == 0 else "PAIRWISE_PARTITION_MISMATCH",
                "authority":"DIAGNOSTIC_ONLY_GRAPH_STROKES_NOT_CLASSIFIED_AS_ELECTRICAL_WIRES",
                "mismatch_examples":mismatch_examples,
            },
            "independently_verified_electrical_memberships":0,
            "status":"DIAGNOSTIC_ONLY_ELECTRICAL_SIGNOFF_BLOCKED",
            "rows":rows}
