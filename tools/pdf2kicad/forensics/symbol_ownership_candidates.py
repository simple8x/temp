#!/usr/bin/env python3
"""Evidence-constrained geometric core for QI9 schematic reconstruction.

This replaces endpoint-only connected components + nearest-bbox ranking with:
* intersection-aware segment topology (endpoint/endpoint and endpoint/interior);
* interior/interior crossings kept separate unless explicit junction evidence exists;
* per-page connected-component evidence with original segment IDs;
* one-to-one global reference/component assignment (Hungarian algorithm);
* ambiguity ledger and strict UNKNOWN authority (no automatic electrical promotion).

Important: this is a candidate resolver, not a proof engine. PDF strokes alone do
not always encode electrical junction semantics; all ownership/membership remains
UNKNOWN until independently reviewed or corroborated.
"""
from __future__ import annotations
import argparse, json, math
from collections import defaultdict
from pathlib import Path

EPS = 1e-7

def dist(a, b):
    return math.hypot(a[0]-b[0], a[1]-b[1])

def point_segment(p, a, b, tol):
    dx, dy = b[0]-a[0], b[1]-a[1]
    den = dx*dx + dy*dy
    if den <= EPS:
        return (dist(p, a) <= tol, 0.0, a)
    t = ((p[0]-a[0])*dx + (p[1]-a[1])*dy) / den
    tc = min(1.0, max(0.0, t))
    q = (a[0]+tc*dx, a[1]+tc*dy)
    return (dist(p, q) <= tol and -EPS <= t <= 1+EPS, tc, q)

class DSU:
    def __init__(self, n):
        self.p=list(range(n))
        self.rank=[0]*n
    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x
    def union(self, a, b):
        a,b=self.find(a),self.find(b)
        if a==b: return
        if self.rank[a] < self.rank[b]: a,b=b,a
        self.p[b]=a
        if self.rank[a] == self.rank[b]: self.rank[a]+=1

def bbox(s):
    return [min(s["a"][0],s["b"][0]), min(s["a"][1],s["b"][1]),
            max(s["a"][0],s["b"][0]), max(s["a"][1],s["b"][1])]

def bbox_distance(p, b):
    dx=max(b[0]-p[0],0.0,p[0]-b[2])
    dy=max(b[1]-p[1],0.0,p[1]-b[3])
    return math.hypot(dx,dy)

def topology(segments, snap_tol=0.15):
    """Return segment components and explicit/ambiguous intersection evidence."""
    n=len(segments)
    dsu=DSU(n)
    boxes=[bbox(s) for s in segments]
    # Sweep on min-X; prune inactive boxes. This avoids unconditional O(n^2)
    # for normal page drawings while retaining exact pair checks for overlaps.
    order=sorted(range(n), key=lambda i:(boxes[i][0],boxes[i][1],i))
    active=[]
    counts=defaultdict(int)
    intersections=[]
    for i in order:
        bi=boxes[i]
        active=[j for j in active if boxes[j][2] >= bi[0]-snap_tol]
        si=segments[i]
        for j in active:
            bj=boxes[j]
            if bj[3] < bi[1]-snap_tol or bi[3] < bj[1]-snap_tol:
                continue
            sj=segments[j]
            endpoints_i=(si["a"],si["b"])
            endpoints_j=(sj["a"],sj["b"])
            joined=False
            # Endpoint-to-endpoint contact.
            for pi in endpoints_i:
                for pj in endpoints_j:
                    if dist(pi,pj) <= snap_tol:
                        joined=True
            # T junction: an endpoint of either segment lands on the interior
            # or endpoint of the other. Interior/interior crossing is NOT joined.
            t_hits=[]
            for p in endpoints_i:
                ok,t,q=point_segment(p,sj["a"],sj["b"],snap_tol)
                if ok: t_hits.append((p,q,t))
            for p in endpoints_j:
                ok,t,q=point_segment(p,si["a"],si["b"],snap_tol)
                if ok: t_hits.append((p,q,t))
            if joined or t_hits:
                dsu.union(i,j)
                counts["endpoint_or_T_contact"] += 1
                intersections.append({"segment_ids":[sj.get("id",j),si.get("id",i)],
                                      "relation":"ENDPOINT_OR_T_CONTACT",
                                      "topology":"CONNECTED_CANDIDATE"})
            else:
                # Record proper geometric crossings as ambiguous, without
                # joining them: a crossing is not proof of a junction.
                a,b=si["a"],si["b"]; c,d=sj["a"],sj["b"]
                den=(b[0]-a[0])*(d[1]-c[1])-(b[1]-a[1])*(d[0]-c[0])
                if abs(den)>EPS:
                    t=((c[0]-a[0])*(d[1]-c[1])-(c[1]-a[1])*(d[0]-c[0]))/den
                    u=((c[0]-a[0])*(b[1]-a[1])-(c[1]-a[1])*(b[0]-a[0]))/den
                    if EPS<t<1-EPS and EPS<u<1-EPS:
                        counts["interior_crossing_ambiguous"] += 1
                        intersections.append({"segment_ids":[sj.get("id",j),si.get("id",i)],
                                              "relation":"INTERIOR_CROSSING",
                                              "topology":"UNKNOWN_CROSSING_NOT_JOINED"})
        active.append(i)
    groups=defaultdict(list)
    for i,s in enumerate(segments):
        groups[dsu.find(i)].append(s)
    comps=[]
    for cid,items in enumerate(groups.values()):
        bb=[min(bbox(s)[0] for s in items),min(bbox(s)[1] for s in items),
            max(bbox(s)[2] for s in items),max(bbox(s)[3] for s in items)]
        comps.append({"component_id":cid,"segment_ids":[s.get("id",i) for i,s in enumerate(items)],
                      "segment_count":len(items),"bbox_pdf_pt":[round(v,4) for v in bb],
                      "topology_status":"GEOMETRIC_COMPONENT_CANDIDATE"})
    return comps, dict(counts), intersections

def hungarian(cost):
    """Minimum-cost rectangular assignment; rows <= columns."""
    n=len(cost)
    if not n: return []
    m=len(cost[0])
    if n>m: raise ValueError("assignment requires columns >= rows")
    u=[0.0]*(n+1); v=[0.0]*(m+1); p=[0]*(m+1); way=[0]*(m+1)
    for i in range(1,n+1):
        p[0]=i; j0=0; minv=[float("inf")]*(m+1); used=[False]*(m+1)
        while True:
            used[j0]=True; i0=p[j0]; delta=float("inf"); j1=0
            for j in range(1,m+1):
                if not used[j]:
                    cur=cost[i0-1][j-1]-u[i0]-v[j]
                    if cur<minv[j]: minv[j]=cur; way[j]=j0
                    if minv[j]<delta: delta=minv[j]; j1=j
            for j in range(m+1):
                if used[j]: u[p[j]]+=delta; v[j]-=delta
                else: minv[j]-=delta
            j0=j1
            if p[j0]==0: break
        while True:
            j1=way[j0]; p[j0]=p[j1]; j0=j1
            if j0==0: break
    ans=[-1]*n
    for j in range(1,m+1):
        if p[j]: ans[p[j]-1]=j-1
    return ans

def resolve_references(refs, comps, radius):
    """Global one-to-one candidate assignment; never asserts ownership."""
    if not refs:
        return [], {"matched_candidate_pairs":0,"unassigned_references":0}
    # Keep at most 12 local geometric candidates per reference. Dummy columns
    # allow a reference to remain unassigned rather than force a bad match.
    candidates=[]
    for r in refs:
        p=r["center_pdf_pt"]
        ranked=[]
        for c in comps:
            d=bbox_distance(p,c["bbox_pdf_pt"])
            if d<=radius:
                bb=c["bbox_pdf_pt"]
                w=max(0.0,bb[2]-bb[0]); h=max(0.0,bb[3]-bb[1])
                # Mild shape penalty only ranks candidates; it is not evidence.
                penalty=0.0 if c["segment_count"]>=3 else 6.0
                penalty += max(0.0, math.log1p(max(0.0,w*h))-9.0)*0.15
                ranked.append((d+penalty,d,c))
        ranked.sort(key=lambda x:(x[0],x[2]["component_id"]))
        candidates.append(ranked[:12])
    real_ids=sorted({x[2]["component_id"] for row in candidates for x in row})
    col_index={cid:i for i,cid in enumerate(real_ids)}
    # A dummy per reference; cost > any admissible real candidate.
    dummy_cost=radius+25.0
    matrix=[]
    for row in candidates:
        vals=[dummy_cost]*len(real_ids)
        for cost,d,c in row:
            vals[col_index[c["component_id"]]]=cost
        vals.extend([dummy_cost + k*1e-5 for k in range(len(refs))])
        matrix.append(vals)
    assignment=hungarian(matrix)
    output=[]
    matched=0
    for idx,(ref,row,col) in enumerate(zip(refs,candidates,assignment)):
        ranked=[{"component_id":c["component_id"],"cost":round(cost,4),
                 "distance_to_bbox_pt":round(d,4),"segment_count":c["segment_count"],
                 "bbox_pdf_pt":c["bbox_pdf_pt"],"evidence":"GEOMETRIC_PROXIMITY_ONLY"}
                for cost,d,c in row]
        selected=None
        if col < len(real_ids):
            selected=real_ids[col]
            # Ensure this ref actually had that candidate (not a placeholder).
            if not any(x[2]["component_id"]==selected for x in row):
                selected=None
        if selected is not None: matched+=1
        best=ranked[0]["cost"] if ranked else None
        second=ranked[1]["cost"] if len(ranked)>1 else None
        margin=round(second-best,4) if best is not None and second is not None else None
        output.append({
            "reference_text":ref["text"],"ocr_bbox_pdf_pt":ref.get("bbox_pdf_pt"),
            "global_assignment_component_candidate":selected,
            "assignment_status":"CANDIDATE_ONLY" if selected is not None else "UNASSIGNED",
            "candidate_margin":margin,
            "ambiguity":"AMBIGUOUS" if selected is None or (margin is not None and margin<2.0) else "REQUIRES_SOURCE_REVIEW",
            "symbol_ownership":"UNKNOWN","electrical_membership":"UNKNOWN",
            "candidates":ranked
        })
    return output, {"matched_candidate_pairs":matched,"unassigned_references":len(refs)-matched}

def run(source,out,tolerance,radius):
    data=json.loads(Path(source).read_text(encoding="utf-8"))
    pages=[]; totals=defaultdict(int); exception_ledger=[]
    for page in data.get("pages_result",[]):
        segs=page.get("segments",[])
        comps,topo_counts,intersections=topology(segs,tolerance)
        refs=[t for t in page.get("tokens",[]) if t.get("kind")=="REFERENCE_CANDIDATE"]
        resolved,assign_counts=resolve_references(refs,comps,radius)
        for k,v in topo_counts.items(): totals[k]+=v
        for k,v in assign_counts.items(): totals[k]+=v
        for r in resolved:
            if r["assignment_status"]!="CANDIDATE_ONLY" or r["ambiguity"]=="AMBIGUOUS":
                exception_ledger.append({"page":page["page"],"reference":r["reference_text"],
                                         "reason":"NO_UNIQUE_GEOMETRIC_CANDIDATE",
                                         "authority":"UNKNOWN"})
            else:
                exception_ledger.append({"page":page["page"],"reference":r["reference_text"],
                                         "reason":"CANDIDATE_REQUIRES_PDF_SOURCE_REVIEW",
                                         "authority":"UNKNOWN"})
        pages.append({"page":page["page"],"segment_count":len(segs),
                      "geometric_components":len(comps),"topology_counts":topo_counts,
                      "assignment_counts":assign_counts,"references":resolved,
                      "components":comps,"intersection_evidence":intersections,"segments":segs,"tokens":page.get("tokens",[])})
    summary={"pages":len(pages),"reference_tokens":sum(len(p["references"]) for p in pages),
             "geometric_components":sum(p["geometric_components"] for p in pages),
             **dict(totals)}
    result={
      "schema":"qi9-ecsr-geometric-core/v2","source_sha256":data.get("source_sha256"),
      "input_schema":data.get("schema"),
      "algorithm":{"topology":"sweep-line bbox pruning + endpoint/T-contact union; interior crossings remain separate",
                   "reference_resolution":"global one-to-one Hungarian assignment with unassigned dummy columns",
                   "authority":"candidate generation only; no automatic promotion"},
      "parameters":{"endpoint_snap_tolerance_pt":tolerance,"reference_bbox_radius_pt":radius},
      "summary":summary,"pages":pages,"exception_ledger":exception_ledger,
      "authority":{"reference_to_symbol":"UNKNOWN","pin_identity":"UNKNOWN",
                   "electrical_connectivity":"UNKNOWN","auto_promotion":False},
      "signoff":"BLOCKED_SOURCE_REVIEW_AND_PIN_CONNECTIVITY_PROOF_REQUIRED"
    }
    Path(out).parent.mkdir(parents=True,exist_ok=True)
    Path(out).write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps({"summary":summary,"authority":result["authority"],
                      "signoff":result["signoff"],"exception_count":len(exception_ledger)},indent=2))
    return result

def self_test():
    segs=[
      {"id":0,"a":[0,0],"b":[10,0]},
      {"id":1,"a":[10,0],"b":[10,5]},       # endpoint contact
      {"id":2,"a":[5,-5],"b":[5,5]},        # interior crossing: separate
      {"id":3,"a":[20,0],"b":[30,0]},
      {"id":4,"a":[25,0],"b":[25,5]},       # T contact
    ]
    comps,counts,ints=topology(segs,0.15)
    assert len(comps)==3, (len(comps),comps)
    assert counts.get("interior_crossing_ambiguous",0)>=1
    assert counts.get("endpoint_or_T_contact",0)>=2
    assert sorted(c["segment_count"] for c in comps)==[1,2,2]
    assert hungarian([[1,4,100],[2,1,100]])==[0,1]
    refs=[{"text":"R1","center_pdf_pt":[1,1],"bbox_pdf_pt":[0,0,2,2]},
          {"text":"R2","center_pdf_pt":[1,1],"bbox_pdf_pt":[0,0,2,2]}]
    rr,cc=resolve_references(refs,[{"component_id":0,"segment_count":4,"bbox_pdf_pt":[0,0,2,2]}],35)
    assert sum(x["global_assignment_component_candidate"] is not None for x in rr)==1
    assert all(x["symbol_ownership"]=="UNKNOWN" for x in rr)
    print("SELF_TEST=PASS")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--input")
    ap.add_argument("--output",default="symbol_ownership_candidates.json")
    ap.add_argument("--snap-tolerance-pt",type=float,default=0.15)
    ap.add_argument("--reference-radius-pt",type=float,default=35.0)
    ap.add_argument("--self-test",action="store_true")
    a=ap.parse_args()
    if a.self_test: self_test()
    elif a.input: run(a.input,a.output,a.snap_tolerance_pt,a.reference_radius_pt)
    else: ap.error("--input required unless --self-test")
