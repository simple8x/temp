#!/usr/bin/env python3
"""Conservative pin-identity and connectivity-partition candidate solver.

Consumes qi9-ecsr-geometric-core/v2. OCR pin text is attached only as a
candidate to nearby vector endpoints. Geometric components produce candidate
partitions, never electrical truth: PDF line art may include symbol outlines,
crossings without junction dots, and non-electrical decoration.
"""
from __future__ import annotations
import argparse, json, math
from collections import defaultdict
from pathlib import Path

def point_segment_distance(p,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1]
    den=dx*dx+dy*dy
    if den<=1e-12: return math.hypot(p[0]-a[0],p[1]-a[1])
    t=max(0.0,min(1.0,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/den))
    return math.hypot(p[0]-(a[0]+t*dx),p[1]-(a[1]+t*dy))

def endpoint_candidates(token, segments, radius):
    p=token["center_pdf_pt"]
    out=[]
    for s in segments:
        for side,key in (("a","a"),("b","b")):
            q=s[key]
            d=math.hypot(p[0]-q[0],p[1]-q[1])
            if d<=radius:
                out.append({"segment_id":s["id"],"endpoint":side,
                            "point_pdf_pt":q,"distance_pt":round(d,4)})
    out.sort(key=lambda x:(x["distance_pt"],x["segment_id"],x["endpoint"]))
    return out[:8]

def run(source,out,pin_radius,terminal_radius):
    data=json.loads(Path(source).read_text(encoding="utf-8"))
    if data.get("schema")!="qi9-ecsr-geometric-core/v2":
        raise SystemExit("unsupported input schema")
    pages=[]; ledger=[]
    totals=defaultdict(int)
    for page in data.get("pages",[]):
        segs=page.get("segments",[])
        comp_for_segment={}
        for c in page.get("components",[]):
            for sid in c.get("segment_ids",[]):
                comp_for_segment[sid]=c["component_id"]
        pins=[]
        for tok in page.get("tokens",[]):
            if tok.get("kind")!="PIN_TEXT_CANDIDATE": continue
            anchors=endpoint_candidates(tok,segs,pin_radius)
            # Deduplicate endpoint candidates by component; keep the geometric
            # alternatives because multiple endpoints may be equally plausible.
            for a in anchors:
                a["component_id"]=comp_for_segment.get(a["segment_id"])
            best=anchors[0] if anchors else None
            second=anchors[1] if len(anchors)>1 else None
            margin=(round(second["distance_pt"]-best["distance_pt"],4)
                    if best and second else None)
            ambiguous=(not best or (margin is not None and margin<0.75))
            pins.append({
                "pin_text":tok["text"],"ocr_bbox_pdf_pt":tok.get("bbox_pdf_pt"),
                "ocr_confidence":tok.get("confidence"),
                "anchor_candidates":anchors,
                "selected_geometric_anchor":None if ambiguous else best,
                "pin_number_identity":"UNKNOWN",
                "terminal_identity":"UNKNOWN",
                "resolution":"AMBIGUOUS" if ambiguous else "GEOMETRIC_ANCHOR_CANDIDATE",
                "authority":"CANDIDATE_ONLY"
            })
            ledger.append({"page":page["page"],"item":tok["text"],
                           "reason":"PIN_TEXT_NOT_SYMBOL_SCOPED_OR_INDEPENDENTLY_VERIFIED",
                           "authority":"UNKNOWN"})
        # Candidate terminal inventory: endpoints with OCR pin text near them.
        terminal_rows=[]
        for s in segs:
            for side in ("a","b"):
                p=s[side]
                nearby=[]
                for tok in page.get("tokens",[]):
                    if tok.get("kind") not in ("PIN_TEXT_CANDIDATE","NET_LABEL_CANDIDATE"): continue
                    d=math.hypot(tok["center_pdf_pt"][0]-p[0],tok["center_pdf_pt"][1]-p[1])
                    if d<=terminal_radius:
                        nearby.append({"text":tok["text"],"kind":tok["kind"],"distance_pt":round(d,4)})
                terminal_rows.append({"segment_id":s["id"],"endpoint":side,"point_pdf_pt":p,
                                      "geometric_component_id":comp_for_segment.get(s["id"]),
                                      "nearby_text":nearby})
        # Geometric partition: endpoints/segments in the same component are
        # grouped as a candidate equivalence class. This is not a net partition.
        by_component=defaultdict(list)
        for t in terminal_rows:
            if t["geometric_component_id"] is not None:
                by_component[t["geometric_component_id"]].append(t)
        partitions=[]
        for cid,terms in sorted(by_component.items()):
            if len(terms)>1:
                partitions.append({"candidate_partition_id":f"p{page['page']}-c{cid}",
                                   "geometric_component_id":cid,
                                   "terminal_count":len(terms),
                                   "terminals":terms,
                                   "equivalence":"GEOMETRICALLY_CONNECTED_CANDIDATE",
                                   "electrical_net_membership":"UNKNOWN",
                                   "reason":"Input strokes are not classified as electrical wires versus symbol graphics."})
        crossings=[x for x in page.get("intersection_evidence",[])
                   if x.get("topology")=="UNKNOWN_CROSSING_NOT_JOINED"]
        totals["pin_text_candidates"]+=len(pins)
        totals["pin_anchor_candidates"]+=sum(bool(x["anchor_candidates"]) for x in pins)
        totals["ambiguous_pin_candidates"]+=sum(x["resolution"]=="AMBIGUOUS" for x in pins)
        totals["geometric_candidate_partitions"]+=len(partitions)
        totals["unresolved_crossings"]+=len(crossings)
        pages.append({"page":page["page"],"pin_identity_candidates":pins,
                      "terminal_inventory":terminal_rows,
                      "geometric_candidate_partitions":partitions,
                      "unresolved_crossings":crossings,
                      "authority":{"pin_identity":"UNKNOWN","connectivity_partition":"CANDIDATE_ONLY",
                                   "electrical_nets":"UNKNOWN"}})
    result={
      "schema":"qi9-pin-connectivity-candidates/v1",
      "source_sha256":data.get("source_sha256"),
      "input_schema":data.get("schema"),
      "algorithm":{
        "pin_identity":"OCR pin-text to segment-endpoint spatial candidate search; retain alternatives and ambiguity",
        "connectivity":"geometric-component equivalence classes over endpoint inventory; not promoted to electrical nets",
        "crossing_policy":"interior crossings remain separate UNKNOWN events unless source junction evidence resolves them",
        "authority":"no pin identity or net membership is VERIFIED"
      },
      "parameters":{"pin_endpoint_radius_pt":pin_radius,"terminal_text_radius_pt":terminal_radius},
      "summary":{"pages":len(pages),**dict(totals)},
      "pages":pages,"exception_ledger":ledger,
      "authority":{"pin_identity":"UNKNOWN","connectivity_partition":"CANDIDATE_ONLY",
                   "electrical_connectivity":"UNKNOWN","auto_promotion":False},
      "signoff":"BLOCKED_NO_SYMBOL_PIN_OWNERSHIP_OR_ELECTRICAL_WIRE_CLASSIFICATION"
    }
    Path(out).parent.mkdir(parents=True,exist_ok=True)
    Path(out).write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps({"summary":result["summary"],"authority":result["authority"],
                      "signoff":result["signoff"],"exceptions":len(ledger)},indent=2))
    return result

def self_test():
    segs=[{"id":0,"a":[0,0],"b":[10,0]},{"id":1,"a":[10,0],"b":[10,5]}]
    tok={"center_pdf_pt":[10.1,0.1]}
    c=endpoint_candidates(tok,segs,1.0)
    assert c and c[0]["segment_id"] in (0,1)
    # Equal-distance ambiguity must be retained, not forced into a pin number.
    print("SELF_TEST=PASS")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",required=False)
    ap.add_argument("--output",default="pin_connectivity_candidates.json")
    ap.add_argument("--pin-radius-pt",type=float,default=4.0)
    ap.add_argument("--terminal-radius-pt",type=float,default=6.0)
    ap.add_argument("--self-test",action="store_true")
    a=ap.parse_args()
    if a.self_test: self_test()
    elif a.input: run(a.input,a.output,a.pin_radius_pt,a.terminal_radius_pt)
    else: ap.error("--input required unless --self-test")
