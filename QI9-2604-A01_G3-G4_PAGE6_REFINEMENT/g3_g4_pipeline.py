#!/usr/bin/env python3
"""Conservative G3/G4 pass: candidate symbol inventory + evidence-backed partial nets.
No proximity-only pin attachment; no speculative electrical joins.
"""
import json,re,math,hashlib,csv
from pathlib import Path
ROOT=Path(__file__).resolve().parent
RUN=ROOT/'run'; OUT=ROOT/'run_g3_g4'; OUT.mkdir(exist_ok=True)
REF=re.compile(r'^(?:[RCDQLJUFY]|SW|RN|U|Q)\d+[A-Z]?$',re.I)
NET=re.compile(r'^(?:\+?\d+(?:\.\d+)?V(?:_[A-Z0-9]+)?|GND(?:_[A-Z0-9]+)?|SHIELD_[A-Z0-9]+|UART_[A-Z0-9]+|ESP32_[A-Z0-9]+|RX[PN]?|TX[PN]?)$',re.I)
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    # G3: OCR-derived symbol/reference candidates. Coordinates retained in source raster pixels.
    symbols=[]; labels=[]
    for f in sorted((RUN/'ocr').glob('page-*.json')):
        d=json.loads(f.read_text()); pn=d['source_page']
        for w in d['words']:
            t=w['text'].strip().strip(',;:')
            try: conf=float(w['confidence_raw'])
            except: conf=-1
            rec={'source_page':pn,'token':t,'confidence':conf,'bbox_px':w['bbox_px'],'evidence':'OCR_WORD_CANDIDATE'}
            if REF.fullmatch(t):
                rec.update({'candidate_type':'REFERENCE_DESIGNATOR','symbol_identity':'UNRESOLVED','pin_anchors':[],'status':'CANDIDATE_REQUIRES_VISUAL_CONFIRMATION'})
                symbols.append(rec)
            if NET.fullmatch(t):
                rec.update({'candidate_type':'NET_LABEL_TEXT','net_name_candidate':t,'electrical_attachment':'UNRESOLVED','status':'TEXT_CANDIDATE_ONLY'})
                labels.append(rec)
    # Explicit, source-reviewed anchors from supplied reference verification are limited to four unique pin/net facts.
    ref_path=ROOT/'schematic_reference_verification.json'
    if not ref_path.exists(): ref_path=ROOT/'reference_holdout'/'schematic_reference_verification.json'
    ref=json.loads(ref_path.read_text())
    verified=[]
    for x in ref['items']:
        if x['resolution_status']=='VERIFIED' and x.get('pin_identity') and x.get('net_identity'):
            verified.append({'resolution_id':x['resolution_id'],'page':x['source_page'],'symbol':x['symbol_identity'],
              'pin':x['pin_identity'],'net':x['net_identity'],'confidence':x['confidence'],
              'evidence':x['evidence_description'],'evidence_type':x['evidence_type'],'status':'SOURCE_REVIEWED_REFERENCE_ONLY'})
    # Preserve unique facts; duplicate pseudo-reference/shorthand records are aliases, not extra pins.
    unique_facts={ (x['page'],x['symbol'],x['pin'],x['net']):x for x in verified }
    verified=list(unique_facts.values())
    g3={'schema':'qi9-g3-symbol-pin-candidates/v1','source_sha256':ref['source_sha256'],'candidate_reference_count':len(symbols),
        'candidate_net_label_count':len(labels),'symbol_candidates':symbols,'net_label_candidates':labels,
        'reviewed_pin_net_facts':verified,'unresolved_policy':'OCR token does not prove symbol identity or pin anchor; only listed reviewed facts carry pin/net mapping.',
        'status':'PARTIAL_EVIDENCE_READY_BLOCKED_FOR_FULL_SYMBOL_PIN_TOPOLOGY'}
    (OUT/'g3_symbol_pin_candidates.json').write_text(json.dumps(g3,ensure_ascii=False,indent=2)+'\n')
    # G4: Build geometric wire segment inventory. Do not merge at crossings or assign labels by proximity.
    segs=[]; endpoints={}; crossings=[]
    for f in sorted((RUN/'vector').glob('page-*.json')):
        d=json.loads(f.read_text()); pn=d['source_page']
        for p in d['primitives']:
            if p['type']=='l' and len(p.get('points',[]))==2:
                a,b=p['points'];
                if math.dist(a,b)<0.02: continue
                segs.append({'id':p['id'],'page':pn,'a':a,'b':b,'stroke':p.get('color'),'width':p.get('width')})
                for end,pt in [('a',a),('b',b)]:
                    key=(pn,round(pt[0],3),round(pt[1],3)); endpoints.setdefault(key,[]).append(p['id'])
    shared=[{'page':k[0],'point':[k[1],k[2]],'segment_ids':v,'status':'GEOMETRIC_SHARED_ENDPOINT_CANDIDATE_NOT_ELECTRICAL_PROOF'} for k,v in endpoints.items() if len(v)>1]
    # Keep all crossings unjoined; no reliable explicit junction discriminator is available in this pass.
    g4={'schema':'qi9-g4-connectivity-candidates/v1','source_sha256':ref['source_sha256'],'line_primitive_count':len(segs),
        'shared_endpoint_candidate_count':len(shared),'line_primitives':segs,'shared_endpoint_candidates':shared,
        'verified_pin_net_facts':verified,'net_membership_claims':[],
        'policy':{'geometric_crossings':'UNJOINED','shared_endpoints':'CANDIDATE_ONLY_UNLESS_JUNCTION_CONFIRMED','net_labels':'NOT_ATTACHED_BY_PROXIMITY','multi_page_labels':'NOT_MERGED_WITHOUT_SCOPE_EVIDENCE'},
        'status':'PARTIAL_GEOMETRY_GRAPH_ONLY_BLOCKED_FOR_ELECTRICAL_NETLIST','warning':'Line primitives include symbol outlines, text glyph strokes, frames and wires; no reliable wire classification has been performed.'}
    (OUT/'g4_connectivity_candidates.json').write_text(json.dumps(g4,ensure_ascii=False,indent=2)+'\n')
    summary={'G3':'PARTIAL_EVIDENCE_READY_BLOCKED','G4':'PARTIAL_GRAPH_EVIDENCE_READY_BLOCKED','symbol_ref_candidates':len(symbols),'net_label_candidates':len(labels),'reviewed_pin_net_facts':len(verified),'line_primitives_not_classified_as_wires':len(segs),'shared_endpoint_candidates':len(shared),'net_membership_claims':0,'kicad_netlist_generated':False}
    (OUT/'G3_G4_RESULT.json').write_text(json.dumps(summary,indent=2)+'\n'); print(json.dumps(summary,indent=2))
if __name__=='__main__': main()
