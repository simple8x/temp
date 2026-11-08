#!/usr/bin/env python3
"""Forensic inventory of native PDF drawing objects and stroke-font exclusion.

Preserves PyMuPDF drawing/path boundaries and style metadata instead of flattening
everything into untyped line segments. No semantic role or electrical net is asserted.
"""
from __future__ import annotations
import argparse, hashlib, json
from collections import Counter
from pathlib import Path
import pymupdf

EXPECTED = "a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc"


def _box_overlap(a, b, pad=0.25):
    return not (a[2] < b[0]-pad or b[2] < a[0]-pad or
                a[3] < b[1]-pad or b[3] < a[1]-pad)


def _item_record(item, ox, oy):
    kind=item[0]
    def pt(p): return [round(float(p.x-ox),4),round(float(p.y-oy),4)]
    if kind=="l":
        return {"kind":"line","points":[pt(item[1]),pt(item[2])]}
    if kind=="c":
        return {"kind":"cubic","points":[pt(p) for p in item[1:5]]}
    if kind=="re":
        r=item[1]
        return {"kind":"rect","bbox":[round(r.x0-ox,4),round(r.y0-oy,4),round(r.x1-ox,4),round(r.y1-oy,4)]}
    if kind=="qu":
        q=item[1]
        return {"kind":"quad","points":[pt(q.ul),pt(q.ur),pt(q.lr),pt(q.ll)]}
    return {"kind":str(kind),"raw_type":str(type(item).__name__)}


def audit(pdf, page_no, outdir, dpi=120):
    raw=Path(pdf).read_bytes()
    digest=hashlib.sha256(raw).hexdigest()
    if digest!=EXPECTED: raise SystemExit(f"source SHA mismatch: {digest}")
    doc=pymupdf.open(pdf)
    page=doc[page_no-1]
    mb=page.mediabox; ox,oy=mb.x0,mb.y0
    words=[]
    for w in page.get_text("words"):
        words.append({"text":str(w[4]),"bbox":[round(w[0]-ox,3),round(w[1]-oy,3),round(w[2]-ox,3),round(w[3]-oy,3)],"source":"pdf_text_layer"})
    from pdf2kicad.independent_pdf_reconstruction import _decode_vector_text_independently
    vector_words=_decode_vector_text_independently(pdf,page_no-1)
    for w in vector_words:
        words.append({"text":w["text"],"bbox":w["bbox"],"source":"stroke_font_newstroke","score":w["decode_score"]})
    drawings=page.get_drawings()
    paths=[]; item_counts=Counter(); style_counts=Counter()
    for i,d in enumerate(drawings):
        rect=d.get("rect")
        bbox=None if rect is None else [round(rect.x0-ox,3),round(rect.y0-oy,3),round(rect.x1-ox,3),round(rect.y1-oy,3)]
        items=[_item_record(x,ox,oy) for x in d.get("items",[])]
        kinds=Counter(x["kind"] for x in items); item_counts.update(kinds)
        style=(str(d.get("type")),str(d.get("color")),str(d.get("fill")),round(float(d.get("width") or 0),3),bool(d.get("closePath")))
        style_counts[style]+=1
        overlaps=[w for w in words if bbox and _box_overlap(bbox,w["bbox"],0.5)]
        paths.append({
          "path_id":i,"bbox":bbox,"drawing_type":d.get("type"),
          "stroke_color":d.get("color"),"fill_color":d.get("fill"),
          "stroke_width_pt":d.get("width"),"line_cap":d.get("lineCap"),
          "line_join":d.get("lineJoin"),"close_path":d.get("closePath"),
          "even_odd":d.get("even_odd"),"seqno":d.get("seqno"),
          "layer":d.get("layer"),"level":d.get("level"),
          "items":items,"item_kind_counts":dict(kinds),
          "overlapping_text_candidates":[{"text":w["text"],"source":w["source"],"bbox":w["bbox"]} for w in overlaps],
          "semantic_role":"UNKNOWN","authority":"CANDIDATE_ONLY"
        })
    out=Path(outdir); out.mkdir(parents=True,exist_ok=True)
    pix=page.get_pixmap(matrix=pymupdf.Matrix(dpi/72,dpi/72),alpha=False)
    image_path=out/f"page{page_no:02d}_source_render.png"; pix.save(image_path)
    report={
      "schema":"qi9-pdf-native-object-audit/v1","source_sha256":digest,"page":page_no,
      "page_size_pt":[round(mb.width,3),round(mb.height,3)],
      "drawing_path_count":len(paths),"item_kind_counts":dict(item_counts),
      "drawing_style_groups":[{"style":list(k),"path_count":v} for k,v in style_counts.most_common()],
      "pdf_text_layer_words":sum(w["source"]=="pdf_text_layer" for w in words),
      "stroke_font_words":sum(w["source"]=="stroke_font_newstroke" for w in words),
      "paths":paths,
      "policy":{"path_boundary_is_not_semantic_identity":True,"text_overlap_is_exclusion_candidate_only":True,
                "wire_classification":"NOT_ATTEMPTED","pin_identity":"UNKNOWN",
                "electrical_membership":"UNKNOWN","manual_ground_truth_rows":0},
      "render":image_path.name
    }
    (out/f"page{page_no:02d}_native_object_audit.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:report[k] for k in ["page","drawing_path_count","item_kind_counts","pdf_text_layer_words","stroke_font_words","render"]},indent=2))
    doc.close()


if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--pdf",required=True); ap.add_argument("--page",type=int,default=6)
    ap.add_argument("--outdir",required=True); ap.add_argument("--dpi",type=int,default=120)
    a=ap.parse_args(); audit(a.pdf,a.page,a.outdir,a.dpi)
