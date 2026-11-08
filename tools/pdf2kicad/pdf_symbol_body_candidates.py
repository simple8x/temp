"""Native PDF symbol-body candidates from quad/rect drawing objects.

Uses source PDF object types that were discarded by the legacy line-only extractor.
Association is geometric and remains CANDIDATE_ONLY; it is not pin/net proof.
"""
from __future__ import annotations
import math


def _bbox(points):
    xs=[p[0] for p in points]; ys=[p[1] for p in points]
    return [min(xs),min(ys),max(xs),max(ys)]


def _gap(a,b):
    dx=max(a[0]-b[2],b[0]-a[2],0.0)
    dy=max(a[1]-b[3],b[1]-a[3],0.0)
    return dx,dy,math.hypot(dx,dy)


def _overlap_ratio(a,b,axis):
    lo=max(a[axis],b[axis]); hi=min(a[axis+2],b[axis+2])
    inter=max(0.0,hi-lo)
    denom=max(1e-9,min(a[axis+2]-a[axis],b[axis+2]-b[axis]))
    return inter/denom


def _associate(ref_box, shape_box, max_vertical_gap=26.0, max_horizontal_gap=18.0):
    dx,dy,distance=_gap(ref_box,shape_box)
    ox=_overlap_ratio(ref_box,shape_box,0)
    oy=_overlap_ratio(ref_box,shape_box,1)
    if dx==0 and dy==0:
        mode="OVERLAPPING"
    elif dx==0 and dy<=max_vertical_gap and ox>=0.60:
        mode="REFERENCE_ABOVE_SHAPE"
    elif dy==0 and dx<=max_horizontal_gap and oy>=0.25:
        mode="REFERENCE_SIDE_OF_SHAPE"
    else:
        return None
    return {"mode":mode,"bbox_gap_pt":round(distance,4),
            "horizontal_overlap_ratio":round(ox,4),
            "vertical_overlap_ratio":round(oy,4),
            "score":round(distance+(1.0-ox)*2.0+(1.0-oy)*0.5,4)}


def discover_symbol_body_candidates(pdf_path, page_no, references):
    import pymupdf
    doc=pymupdf.open(pdf_path)
    page=doc[page_no-1]; mb=page.mediabox
    ox,oy=mb.x0,mb.y0
    shapes=[]
    for path_id,d in enumerate(page.get_drawings()):
        for item_index,item in enumerate(d.get("items",[])):
            kind=item[0]
            if kind=="qu":
                q=item[1]
                points=[[float(p.x-ox),float(p.y-oy)] for p in (q.ul,q.ur,q.lr,q.ll)]
                shape_kind="QUAD"
            elif kind=="re":
                r=item[1]
                points=[[r.x0-ox,r.y0-oy],[r.x1-ox,r.y0-oy],
                        [r.x1-ox,r.y1-oy],[r.x0-ox,r.y1-oy]]
                shape_kind="RECT"
            else:
                continue
            box=_bbox(points)
            w,h=box[2]-box[0],box[3]-box[1]
            if w<2.0 or h<2.0 or w>180.0 or h>180.0:
                continue
            shapes.append({"path_id":path_id,"item_index":item_index,
                           "shape_kind":shape_kind,"points":points,
                           "bbox":[round(v,4) for v in box],
                           "stroke_width_pt":d.get("width"),
                           "fill_color":d.get("fill"),
                           "drawing_type":d.get("type)})
    rows=[]
    for ref in references:
        ref_box=ref["bbox"]
        matches=[]
        for shape in shapes:
            relation=_associate(ref_box,shape["bbox"])
            if relation:
                matches.append({**shape,"association":relation})
        matches.sort(key=lambda x:(x["association"]["score"],x["path_id"],x["item_index"]))
        rows.append({"reference":ref["text"],"reference_bbox":ref_box,
                     "matches":matches[:8],
                     "best_match":matches[0] if matches else None,
                     "symbol_ownership":"CANDIDATE_ONLY"})
    doc.close()
    return {"algorithm":"native_quad_rect_reference_association_v1",
            "native_shape_count":len(shapes),"references":rows,
            "policy":{"pdf_quad_rect_are_native_geometry":True,
                      "reference_proximity_is_not_verified_ownership":True,
                      "pin_identity":"UNKNOWN","net_membership":"UNKNOWN",
                      "electrical_authority":"NONE"}}
