#!/usr/bin/env python3
"""Evidence-first PDF schematic extraction. Never auto-connects electrical nets."""
import argparse, hashlib, json, subprocess, sys
from pathlib import Path
import fitz

SCHEMATIC_PAGES = list(range(5, 12))  # source PDF page numbers, 1-based

def sha256(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

def pt(p): return [round(float(p.x),4), round(float(p.y),4)]
def rect(r): return [round(float(v),4) for v in r]

def extract(pdf, out, dpi=240, do_ocr=True):
    out=Path(out); out.mkdir(parents=True,exist_ok=True)
    geomdir=out/'vector'; geomdir.mkdir(exist_ok=True)
    ocrdir=out/'ocr'; ocrdir.mkdir(exist_ok=True)
    doc=fitz.open(pdf); pages=[]
    for pn in SCHEMATIC_PAGES:
        page=doc[pn-1]; primitives=[]; pid=0
        for di,d in enumerate(page.get_drawings()):
            for ii,it in enumerate(d['items']):
                typ=it[0]; obj={'id':f'p{pn}-d{di}-i{ii}','type':typ,'drawing_index':di,
                    'item_index':ii,'color':d.get('color'),'fill':d.get('fill'),
                    'width':round(float(d.get('width') or 0),4),'closePath':bool(d.get('closePath'))}
                if typ=='l': obj['points']=[pt(it[1]),pt(it[2])]
                elif typ=='c': obj['points']=[pt(x) for x in it[1:5]]
                elif typ=='re': obj['rect']=rect(it[1]); obj['orientation']=it[2]
                elif typ=='qu': obj['points']=[pt(x) for x in (it[1].ul, it[1].ur, it[1].ll, it[1].lr)]
                else: obj['raw_repr']=str(it)
                primitives.append(obj); pid+=1
        spans=[]
        for b in page.get_text('dict').get('blocks',[]):
            for ln in b.get('lines',[]):
                for s in ln.get('spans',[]):
                    if s.get('text','').strip(): spans.append({'text':s['text'],'bbox':rect(s['bbox']),'font':s.get('font'),'size':round(float(s.get('size',0)),3)})
        data={'schema':'pdf-vector-page/v1','source_page':pn,'page_rect':rect(page.rect),
              'primitive_count':len(primitives),'primitives':primitives,'native_text_spans':spans,
              'warning':'Vector strokes are geometry evidence only; no pin/net topology asserted.'}
        (geomdir/f'page-{pn:03}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
        ocr_status='SKIPPED'
        if do_ocr:
            pix=page.get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),alpha=False)
            png=ocrdir/f'page-{pn:03}.png'; pix.save(png)
            try:
                cp=subprocess.run(['tesseract',str(png),'stdout','tsv'],capture_output=True,text=True,check=True)
                (ocrdir/f'page-{pn:03}.tsv').write_text(cp.stdout,encoding='utf-8')
                # Parse word rows only, preserve raw confidence and pixel boxes.
                lines=cp.stdout.splitlines(); words=[]
                if lines:
                    hdr=lines[0].split('\t')
                    for line in lines[1:]:
                        vals=line.split('\t')
                        if len(vals)!=len(hdr): continue
                        row=dict(zip(hdr,vals)); txt=row.get('text','').strip()
                        if txt:
                            words.append({'text':txt,'confidence_raw':row.get('conf'),
                              'bbox_px':[int(row[k]) for k in ('left','top','width','height')],
                              'block_num':row.get('block_num'),'par_num':row.get('par_num'),'line_num':row.get('line_num')})
                (ocrdir/f'page-{pn:03}.json').write_text(json.dumps({'schema':'ocr-word-candidates/v1','source_page':pn,'dpi':dpi,'words':words,'warning':'OCR is candidate text, not verified label association.'},ensure_ascii=False,indent=2)+'\n')
                ocr_status=f'PASS_{len(words)}_WORDS'
            except (FileNotFoundError,subprocess.CalledProcessError) as e: ocr_status=f'FAILED:{type(e).__name__}'
        pages.append({'page':pn,'primitive_count':len(primitives),'native_text_spans':len(spans),'ocr':ocr_status})
    manifest={'schema':'pdf-schematic-extraction/v1','source_file':Path(pdf).name,'source_sha256':sha256(pdf),
      'pdf_pages':len(doc),'schematic_source_pages':SCHEMATIC_PAGES,'pages':pages,
      'stages':{'G0_intake':'PASS_IF_SOURCE_HASH_AND_PAGE_COUNT_MATCH','G1_vector_geometry':'PASS_IF_ALL_7_PAGES_EXTRACTED',
      'G2_text_candidates':'PASS_IF_OCR_COMPLETES_OR_EXPLICITLY_SKIPPED','G3_symbol_pin_topology':'BLOCKED_REQUIRES_REVIEWED_SYMBOL_AND_PIN_ANCHORS',
      'G4_net_connectivity':'BLOCKED_NO_AUTOMATIC_NET_ASSERTION','G5_KICAD_EXPORT':'BLOCKED_NO_VERIFIED_CONNECTIVITY_AND_NO_KICAD_CLI_VALIDATION'},
      'policy':{'crossings':'DO_NOT_JOIN_WITHOUT_JUNCTION_EVIDENCE','labels':'DO_NOT_ATTACH_BY_PROXIMITY_ALONE',
      'pin_anchors':'REQUIRE_SYMBOL_TEMPLATE_OR_MANUAL_REVIEW','unknowns':'PRESERVE_AS_UNRESOLVED','source_mutation':False}}
    (out/'pipeline_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return manifest

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('pdf'); ap.add_argument('--out',required=True); ap.add_argument('--dpi',type=int,default=240); ap.add_argument('--no-ocr',action='store_true'); a=ap.parse_args()
    m=extract(a.pdf,a.out,a.dpi,not a.no_ocr); print(json.dumps({'pages':len(m['pages']),'primitives':sum(x['primitive_count'] for x in m['pages']),'ocr':[x['ocr'] for x in m['pages']]},indent=2))
if __name__=='__main__': main()
