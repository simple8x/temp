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
    endpoints = [(i, p) for i,s in enumerate(segments) for p in (s["a"],s["b"])]
    def dot_near(p):
        return any(math.hypot(p[0]-d[0],p[1]-d[1]) <= junction_radius for d in dots)
    for ix,(i,p) in enumerate(endpoints):
        for j,q in endpoints[ix+1:]:
            if math.hypot(p[0]-q[0],p[1]-q[1]) <= tolerance:
                uf.union(i,j)
    intersections = []
    for i,a in enumerate(segments):
        ax0,ax1=sorted((a["a"][0],a["b"][0])); ay0,ay1=sorted((a["a"][1],a["b"][1]))
        for j in range(i+1,len(segments)):
            b=segments[j]
            if a["axis"] == b["axis"]: continue
            h,v=(a,b) if a["axis"]=="h" else (b,a)
            p=(v["a"][0],h["a"][1])
            if (min(h["a"][0],h["b"][0])-tolerance <= p[0] <= max(h["a"][0],h["b"][0])+tolerance
                and min(v["a"][1],v["b"][1])-tolerance <= p[1] <= max(v["a"][1],v["b"][1])+tolerance):
                endpoint_hit = any(math.hypot(p[0]-e[0],p[1]-e[1])<=tolerance
                                   for e in (h["a"],h["b"],v["a"],v["b"]))
                marked = dot_near(p)
                if marked or (endpoint_hit and dot_near(p)):
                    uf.union(a["id"],b["id"])
                intersections.append({"point":p,"segments":[a["id"],b["id"]],
                                       "explicit_dot_candidate":marked,
                                       "endpoint_contact":endpoint_hit,
                                       "decision":"JOIN" if marked else "CROSSING_OR_T_CONTACT_UNRESOLVED"})
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
    for row in parser_evidence.get("memberships",[]):
        t=row.get("terminal",{})
        d,c=nearest_component(t.get("x",0),t.get("y",0))
        rows.append({"ref":row.get("ref"),"pin":row.get("pin_identity"),"net":row.get("net"),
                     "terminal_index":row.get("terminal_index"),"pdf_component":c,
                     "distance_pt":round(d,4),"status":"GEOMETRY_MATCH_CANDIDATE" if d<=tolerance
                     else "NO_INDEPENDENT_GEOMETRY_MATCH",
                     "electrical_verification":"NOT_ESTABLISHED"})
    return {"schema":"pdf-graph-comparison/v1","parser_rows":len(rows),
            "geometry_candidates":sum(r["status"]=="GEOMETRY_MATCH_CANDIDATE" for r in rows),
            "exceptions":sum(r["status"]!="GEOMETRY_MATCH_CANDIDATE" for r in rows),
            "independently_verified_electrical_memberships":0,
            "status":"DIAGNOSTIC_ONLY_ELECTRICAL_SIGNOFF_BLOCKED",
            "rows":rows}
