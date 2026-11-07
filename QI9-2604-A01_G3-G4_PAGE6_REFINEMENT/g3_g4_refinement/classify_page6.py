#!/usr/bin/env python3
"""Conservative RS232 page-6 stroke ranking; never emits electrical connectivity."""
import fitz, json, math, hashlib, argparse
from pathlib import Path

def run(pdf_path, output_path):
    pdf=Path(pdf_path)
    doc=fitz.open(pdf)
    if len(doc) < 6:
        raise ValueError(f"Expected at least 6 PDF pages; found {len(doc)}")
    page=doc[5]
    drawings=page.get_drawings()
    lines=[]
    marks={}
    for di,d in enumerate(drawings):
        for ii,it in enumerate(d.get('items',[])):
            if it[0]=='l':
                a,b=it[1],it[2]
                ax,ay,bx,by=map(float,[a.x,a.y,b.x,b.y])
                length=math.hypot(bx-ax,by-ay)
                r={'id':f'p6-d{di}-i{ii}','drawing_index':di,'item_index':ii,
                   'a':[round(ax,4),round(ay,4)],'b':[round(bx,4),round(by,4)],
                   'length_pt':round(length,4),'width_pt':round(float(d.get('width') or 0),4),
                   'color':d.get('color'),'fill':d.get('fill'),'closePath':bool(d.get('closePath'))}
                lines.append(r)
                dx,dy=abs(ax-bx),abs(ay-by)
                if r['width_pt'] in (0.48,0.6,0.72) and min(dx,dy)<=0.03 and 3<=length<100:
                    r.update(classification='WIRE_LIKE_STROKE_CANDIDATE',
                             evidence=['stroke_width_heuristic','orthogonal_segment','length_3_to_100pt'],
                             limitations=['may_be_symbol_outline_or_pin_graphic','not_electrical_wire_proof'])
            rect=d.get('rect')
            fill=d.get('fill')
            if it[0]=='c' and rect is not None and fill is not None:
                w,h=abs(float(rect.width)),abs(float(rect.height))
                if 1<=w<=3.5 and 1<=h<=3.5 and abs(w-h)<=0.5:
                    bbox=tuple(round(float(v),3) for v in rect)
                    fill_key=tuple(round(float(v),6) for v in fill)
                    key=(di,bbox,fill_key)
                    if key not in marks:
                        marks[key]={'drawing_index':di,'bbox':list(bbox),'fill':list(fill),
                                    'curve_item_indices':[],
                                    'classification':'SMALL_FILLED_MARK_CANDIDATE_NOT_CONFIRMED_JUNCTION'}
                    marks[key]['curve_item_indices'].append(ii)
    candidates=[x for x in lines if x.get('classification')]
    filled=list(marks.values())
    source_sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
    summary={'source_pdf_sha256':source_sha,'source_page':6,
             'page_dimensions_pt':[page.rect.width,page.rect.height],
             'line_primitives':len(lines),'wire_like_stroke_candidates':len(candidates),
             'small_filled_mark_candidates':len(filled),
             'raw_filled_curve_items':sum(len(x['curve_item_indices']) for x in filled),
             'duplicate_curve_items_collapsed':sum(max(0,len(x['curve_item_indices'])-1) for x in filled),
             'verified_wire_segments':0,'verified_junctions':0,'verified_net_memberships':0,
             'status':'CANDIDATE_CLASSIFICATION_ONLY_BLOCKED_FOR_ELECTRICAL_CONNECTIVITY'}
    result={'schema':'qi9-g4-page6-wire-classification-candidates/v2','summary':summary,
            'classifier':{'rules':'stroke width in {0.48,0.60,0.72} pt; near-horizontal/vertical; length 3..100 pt',
                          'decision':'ranking heuristic only; no primitive promoted to verified wire',
                          'junction_policy':'unique drawing/bbox/fill marks retained as candidates only; curve item repetitions collapsed'},
            'wire_like_candidates':candidates,'filled_mark_candidates':filled}
    Path(output_path).parent.mkdir(parents=True,exist_ok=True)
    Path(output_path).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return summary

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--out',default='page6_wire_classification_candidates.json')
    a=ap.parse_args()
    print(json.dumps(run(a.pdf,a.out),indent=2))
