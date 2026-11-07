#!/usr/bin/env python3
"""Reusable, evidence-preserving intake for schematic PDFs.

Extracts page text, text-span bounding boxes, native vector primitives, and SVG
renders. It does NOT infer electrical connectivity or claim KiCad conversion.
Requires: pymupdf (fitz).
"""
import argparse, hashlib, json
from pathlib import Path
import fitz

def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('pdf',type=Path)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--pages',default='',help='1-based page list, e.g. 5-13 or 1,3,5')
    args=ap.parse_args(); src=args.pdf.resolve(); out=args.out; out.mkdir(parents=True,exist_ok=True)
    if not src.is_file(): ap.error(f'PDF not found: {src}')
    doc=fitz.open(src)
    wanted=set()
    for part in filter(None,args.pages.split(',')):
        if '-' in part:
            a,b=map(int,part.split('-',1)); wanted.update(range(a,b+1))
        else: wanted.add(int(part))
    rows=[]
    for n,page in enumerate(doc,1):
        if wanted and n not in wanted: continue
        words=page.get_text('words',sort=True)
        blocks=page.get_text('dict',sort=True).get('blocks',[])
        spans=[]
        for block in blocks:
            for line in block.get('lines',[]):
                for span in line.get('spans',[]):
                    if span.get('text','').strip():
                        spans.append({'text':span['text'],'bbox':[round(float(v),3) for v in span['bbox']],
                                      'font':span.get('font'),'size':round(float(span.get('size',0)),3)})
        drawings=page.get_drawings(); types={}
        line_primitives=0
        for d in drawings:
            for item in d.get('items',[]):
                typ=item[0]; types[typ]=types.get(typ,0)+1
                if typ=='l': line_primitives+=1
        txt=page.get_text('text',sort=True)
        (out/f'page-{n:03}.txt').write_text(txt,encoding='utf-8')
        (out/f'page-{n:03}.json').write_text(json.dumps({'page':n,'text_spans':spans,'words':[
            {'text':w[4],'bbox':[round(float(v),3) for v in w[:4]]} for w in words]},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        svg_status='EXPORTED'
        try: (out/f'page-{n:03}.svg').write_text(page.get_svg_image(text_as_path=False),encoding='utf-8')
        except Exception as e: svg_status=f'FAILED:{type(e).__name__}:{e}'
        rows.append({'page':n,'width_pt':round(page.rect.width,3),'height_pt':round(page.rect.height,3),
          'rotation':page.rotation,'text_chars':len(txt),'word_count':len(words),'text_span_count':len(spans),
          'drawing_path_count':len(drawings),'line_primitives':line_primitives,'drawing_item_types':types,
          'image_count':len(page.get_images(full=True)),'svg_export':svg_status})
    manifest={'schema':'pdf-only-schematic-intake/v1','input_name':src.name,'input_sha256':sha256(src),
      'input_bytes':src.stat().st_size,'pdf_pages_total':len(doc),'pages_processed':len(rows),
      'engine':'PyMuPDF','pages':rows,'totals':{
       'words':sum(x['word_count'] for x in rows),'text_spans':sum(x['text_span_count'] for x in rows),
       'drawing_paths':sum(x['drawing_path_count'] for x in rows),'line_primitives':sum(x['line_primitives'] for x in rows),
       'images':sum(x['image_count'] for x in rows)},
      'electrical_connectivity':'NOT_INFERRED','kicad_generated':False,
      'status':'INTAKE_EVIDENCE_READY' if rows and any(x['word_count'] or x['drawing_path_count'] or x['image_count'] for x in rows) else 'BLOCKED_NO_CONTENT',
      'limitations':['Text and vector geometry are evidence, not proof of electrical connectivity.',
       'Crossings, junctions, pin anchors, label scope, and multi-page net identity need separate topology inference and verification.',
       'This output is not a KiCad schematic and must not be represented as electrically verified.']}
    (out/'intake_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':manifest['status'],'input_sha256':manifest['input_sha256'],'pages_processed':len(rows),'totals':manifest['totals'],'out':str(out)},indent=2))
    return 0 if manifest['status']=='INTAKE_EVIDENCE_READY' else 3
if __name__=='__main__': raise SystemExit(main())
