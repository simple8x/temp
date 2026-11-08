#!/usr/bin/env python3
"""Run independent PDF forensic inventory without deriving electrical nets."""
import argparse, hashlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pdf2kicad.independent_pdf_reconstruction import extract_independent_pdf_reconstruction
from pdf2kicad.electrical_evidence import classify_pages
from independent_electrical_core import solve as solve_candidate_topology

EXPECTED="a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc"

def classify_strokes_independently(page_result, tolerance=0.35):
    """Conservative raw-PDF stroke-role candidates; never electrical truth."""
    import math
    totals = {"SYMBOL_OUTLINE_CANDIDATE": 0, "SYMBOL_INTERIOR_CANDIDATE": 0,
              "WIRE_CANDIDATE": 0, "AMBIGUOUS_OR_GRAPHIC": 0,
              "OBLIQUE_GRAPHIC_CANDIDATE": 0}
    pages = []
    def dist_to_segment(p, a, b):
        dx, dy = b[0]-a[0], b[1]-a[1]
        den = dx*dx + dy*dy
        t = 0.0 if den == 0 else max(0.0, min(1.0, ((p[0]-a[0])*dx+(p[1]-a[1])*dy)/den))
        return math.hypot(p[0]-a[0]-t*dx, p[1]-a[1]-t*dy)
    for page in page_result:
        segs = page.get("primitive_inventory", [])
        hs, vs = [], []
        for s in segs:
            a,b=s["a"],s["b"]; dx,dy=abs(a[0]-b[0]),abs(a[1]-b[1])
            if dy <= tolerance and dx >= 8:
                hs.append((s,min(a[0],b[0]),max(a[0],b[0]),(a[1]+b[1])/2))
            elif dx <= tolerance and dy >= 8:
                vs.append((s,min(a[1],b[1]),max(a[1],b[1]),(a[0]+b[0])/2))
        rectangles=[]
        for i,h1 in enumerate(hs):
            for h2 in hs[i+1:]:
                if abs(h1[1]-h2[1])>tolerance or abs(h1[2]-h2[2])>tolerance:
                    continue
                x0,x1=(h1[1]+h2[1])/2,(h1[2]+h2[2])/2
                y0,y1=sorted((h1[3],h2[3]))
                if y1-y0<8: continue
                left=any(abs(v[3]-x0)<=tolerance and abs(v[1]-y0)<=tolerance and abs(v[2]-y1)<=tolerance for v in vs)
                right=any(abs(v[3]-x1)<=tolerance and abs(v[1]-y0)<=tolerance and abs(v[2]-y1)<=tolerance for v in vs)
                if left and right: rectangles.append((x0,y0,x1,y1,{h1[0]["id"],h2[0]["id"]}))
        outline=set()
        for x0,y0,x1,y1,hids in rectangles:
            outline.update(hids)
            for v in vs:
                if (abs(v[3]-x0)<=tolerance or abs(v[3]-x1)<=tolerance) and abs(v[1]-y0)<=tolerance and abs(v[2]-y1)<=tolerance:
                    outline.add(v[0]["id"])
        interior=set()
        for s in segs:
            if s["id"] in outline: continue
            a,b=s["a"],s["b"]; sx0,sx1=sorted((a[0],b[0])); sy0,sy1=sorted((a[1],b[1]))
            if any(sx0>=x0+1 and sx1<=x1-1 and sy0>=y0+1 and sy1<=y1-1 for x0,y0,x1,y1,_ in rectangles):
                interior.add(s["id"])
        marks=[m["center"] for m in page.get("junction_mark_candidates",[])]
        rows=[]
        for s in segs:
            if s["id"] in outline: role="SYMBOL_OUTLINE_CANDIDATE"
            elif s["id"] in interior: role="SYMBOL_INTERIOR_CANDIDATE"
            elif s.get("axis")=="OBLIQUE": role="OBLIQUE_GRAPHIC_CANDIDATE"
            elif s.get("axis") in ("H","V") and s.get("length_pt",0)>=3:
                contact=False
                for other in segs:
                    if other["id"]==s["id"] or other["id"] in outline or other["id"] in interior: continue
                    if any(dist_to_segment(p,other["a"],other["b"])<=tolerance for p in (s["a"],s["b"])):
                        contact=True; break
                marked=any(dist_to_segment(m,s["a"],s["b"])<=tolerance for m in marks)
                role="WIRE_CANDIDATE" if contact or marked else "AMBIGUOUS_OR_GRAPHIC"
            else: role="AMBIGUOUS_OR_GRAPHIC"
            totals[role]+=1
            rows.append({"primitive_id":s["id"],"role_candidate":role,"authority":"CANDIDATE_ONLY",
                         "source_path_index":s.get("path_index"),"source_item_index":s.get("item_index")})
        pages.append({"page":page["page"],"rectangular_outline_count":len(rectangles),"primitive_role_candidates":rows})
    return {"algorithm":"independent_raw_pdf_rectangle_exclusion_and_endpoint_contact_v1",
            "tolerance_pt":tolerance,"summary":totals,"pages":pages,
            "authority":"NO_ELECTRICAL_ROLE_VERIFIED",
            "limitations":["Only closed axis-aligned rectangles are detected as outlines.",
              "Contact-supported wire candidates are geometric, not semantic or electrical proof.",
              "Non-rectangular symbol bodies and connected decorative strokes can remain ambiguous.",
              "The classifier is independent of production Topology but shares PyMuPDF source extraction.",
              "No pin identity or net membership is promoted."]}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--pdf",required=True); p.add_argument("--outdir",required=True)
    a=p.parse_args(); pdf=Path(a.pdf); out=Path(a.outdir); out.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
    if digest != EXPECTED: raise SystemExit(f"source hash mismatch: {digest}")
    inv=extract_independent_pdf_reconstruction(str(pdf),list(range(5,12)))
    (out/"independent_pdf_forensic_ir.json").write_text(json.dumps(inv,indent=2)+"\n")
    junctions=classify_pages(pdf,range(5,12),out/"junction_evidence")
    stroke_roles=classify_strokes_independently(inv["page_results"])
    (out/"independent_stroke_role_candidates.json").write_text(json.dumps(stroke_roles,indent=2)+"\n")
    role_by_page={row["page"]:row for row in stroke_roles["pages"]}
    candidate_topology=[]
    for page_ir, junction_page in zip(inv["page_results"], junctions):
        wire_ids={row["primitive_id"] for row in role_by_page[page_ir["page"]]["primitive_role_candidates"]
                  if row["role_candidate"]=="WIRE_CANDIDATE"}
        segments=[{"id":str(p["id"]),"role":"wire","a":p["a"],"b":p["b"]}
                  for p in page_ir["primitive_inventory"] if p["id"] in wire_ids]
        confirmed=[m["center_pt"] for m in junction_page["junction_candidates"]
                   if m["status"]=="EXPLICIT_JUNCTION_CONFIRMED"]
        solved=solve_candidate_topology({"segments":segments,"junctions":confirmed},tolerance=0.35)
        candidate_topology.append({"page":page_ir["page"],"wire_candidate_count":len(segments),
          "junction_confirmed_count":len(confirmed),"candidate_partition_count":len(solved["partitions"]),
          "contact_relation_count":len(solved["evidence"]),"partitions":solved["partitions"],
          "authority":"CANDIDATE_ONLY","electrical_membership":"UNKNOWN"})
    (out/"independent_candidate_topology.json").write_text(json.dumps(candidate_topology,indent=2)+"\n")
    summary={"source_sha256":digest,"pages":7,
      "independent_candidate_topology_pages":len(candidate_topology),
      "independent_candidate_topology_wire_inputs":sum(x["wire_candidate_count"] for x in candidate_topology),
      "independent_candidate_topology_partitions":sum(x["candidate_partition_count"] for x in candidate_topology),
      "independent_stroke_role_candidates":stroke_roles["summary"],
      "reference_candidates":inv["summary"].get("reference_candidates",0),
      "stroke_font_text_words":inv["summary"].get("stroke_font_text_words",0),
      "stroke_font_reference_candidates":inv["summary"].get("stroke_font_reference_candidates",0),
      "stroke_font_pin_token_candidates":inv["summary"].get("stroke_font_pin_token_candidates",0),
      "raw_text_layer_words":inv["summary"].get("raw_text_layer_words",0),
      "vector_primitives":inv["summary"].get("vector_primitives",0),
      "junction_marks_confirmed":sum(x["summary"]["explicit_junctions_confirmed"] for x in junctions),
      "verified_symbol_ownership":0,"verified_pin_identity":0,
      "verified_electrical_memberships":0,
      "electrical_signoff":"BLOCKED_NO_PDF_GROUND_TRUTH_FOR_SYMBOL_PIN_AND_WIRE_CLASSIFICATION"}
    (out/"independent_pdf_audit_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    print(json.dumps(summary,indent=2))
if __name__=="__main__": main()
