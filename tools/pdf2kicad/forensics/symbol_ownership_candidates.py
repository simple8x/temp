#!/usr/bin/env python3
"""Conservative vector-component/reference candidate resolver for QI9.

Builds connected components from PDF vector segment endpoints and ranks OCR
reference-to-component proximity. It NEVER assigns symbol ownership: every
pair remains a candidate and every reference remains UNKNOWN.
"""
from __future__ import annotations
import argparse, json, math
from collections import defaultdict
from pathlib import Path

def components(segments, tolerance=0.8):
    parent=list(range(len(segments)))
    def find(x):
        while parent[x]!=x:
            parent[x]=parent[parent[x]]
            x=parent[x]
        return x
    def union(a,b):
        a,b=find(a),find(b)
        if a!=b: parent[b]=a
    # Spatial hash for endpoints; neighboring buckets handle tolerance boundary.
    buckets=defaultdict(list)
    endpoints=[]
    for i,s in enumerate(segments):
        for p in (s["a"],s["b"]):
            endpoints.append((i,p))
    cell=max(tolerance,0.01)
    for i,p in endpoints:
        key=(math.floor(p[0]/cell),math.floor(p[1]/cell))
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                for j,q in buckets[(key[0]+dx,key[1]+dy)]:
                    if math.hypot(p[0]-q[0],p[1]-q[1])<=tolerance:
                        union(i,j)
        buckets[key].append((i,p))
    groups=defaultdict(list)
    for i,s in enumerate(segments): groups[find(i)].append(s)
    out=[]
    for cid,items in enumerate(groups.values()):
        xs=[v for s in items for v in (s["a"][0],s["b"][0])]
        ys=[v for s in items for v in (s["a"][1],s["b"][1])]
        out.append({"component_id":cid,"segment_count":len(items),
                    "bbox_pdf_pt":[min(xs),min(ys),max(xs),max(ys)]})
    return out

def rect_distance(p,b):
    x=max(b[0]-p[0],0,p[0]-b[2]); y=max(b[1]-p[1],0,p[1]-b[3])
    return math.hypot(x,y)

def run(source,out,tolerance,radius):
    data=json.loads(Path(source).read_text(encoding="utf-8"))
    page_reports=[]; total_refs=total_pairs=0
    for page in data.get("pages_result",[]):
        comps=components(page.get("segments",[]),tolerance)
        refs=[t for t in page.get("tokens",[]) if t.get("kind")=="REFERENCE_CANDIDATE"]
        pairs=[]
        for ref in refs:
            p=ref["center_pdf_pt"]
            ranked=sorted(((rect_distance(p,c["bbox_pdf_pt"]),c) for c in comps), key=lambda item:(item[0],item[1]["component_id"]))
            candidates=[]
            for dist,c in ranked:
                if dist>radius: break
                # Keep raw geometry and score; no symbol ownership assertion.
                candidates.append({"component_id":c["component_id"],
                    "distance_to_bbox_pt":round(dist,4),
                    "component_bbox_pdf_pt":[round(v,4) for v in c["bbox_pdf_pt"]],
                    "component_segment_count":c["segment_count"],
                    "evidence":"OCR_REFERENCE_NEAR_VECTOR_COMPONENT"})
            pairs.append({"reference_text":ref["text"],"ocr_bbox_pdf_pt":ref["bbox_pdf_pt"],
                "candidates":candidates,"symbol_ownership":"UNKNOWN"})
        total_refs+=len(refs); total_pairs+=sum(len(x["candidates"]) for x in pairs)
        page_reports.append({"page":page["page"],"vector_components":len(comps),
            "reference_tokens":len(refs),"candidate_pairs":sum(len(x["candidates"]) for x in pairs),
            "references":pairs,"components":comps})
    result={"schema":"qi9-symbol-ownership-candidates/v1",
      "source_sha256":data.get("source_sha256"),"input_schema":data.get("schema"),
      "parameters":{"endpoint_snap_tolerance_pt":tolerance,"reference_bbox_radius_pt":radius},
      "summary":{"pages":len(page_reports),"reference_tokens":total_refs,
                 "candidate_pairs":total_pairs},
      "authority":{"symbol_ownership":"UNKNOWN","reference_to_symbol":"CANDIDATE_ONLY",
                   "auto_promotion":False,"electrical_connectivity":"NOT_EVALUATED"},
      "signoff":"BLOCKED_SYMBOL_OWNERSHIP_UNVERIFIED","pages":page_reports}
    Path(out).parent.mkdir(parents=True,exist_ok=True)
    Path(out).write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps({"summary":result["summary"],"authority":result["authority"],
                      "signoff":result["signoff"]},indent=2))
    return result

def self_test():
    s=[{"id":0,"a":[0,0],"b":[10,0]},{"id":1,"a":[10.2,0],"b":[10,5]},
       {"id":2,"a":[100,100],"b":[110,100]}]
    c=components(s,0.5)
    assert len(c)==2 and sorted(x["segment_count"] for x in c)==[1,2]
    assert rect_distance([5,2],[0,0,10,1])==1
    print("SELF_TEST=PASS")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--input")
    ap.add_argument("--output",default="symbol_ownership_candidates.json")
    ap.add_argument("--snap-tolerance-pt",type=float,default=0.8)
    ap.add_argument("--reference-radius-pt",type=float,default=35.0)
    ap.add_argument("--self-test",action="store_true")
    a=ap.parse_args()
    if a.self_test:self_test()
    elif a.input:run(a.input,a.output,a.snap_tolerance_pt,a.reference_radius_pt)
    else:ap.error("--input required unless --self-test")
