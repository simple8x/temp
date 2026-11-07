#!/usr/bin/env python3
"""Group every glyph of the given PDF pages into shape classes and write a contact sheet.

Usage: build_font_sheet.py pdf OUT_DIR  PAGE [PAGE ...]      (pages are 1-based)
Writes OUT_DIR/classes.json (class id -> signature, count, sample positions) and OUT_DIR/sheet_N.png.
Label the classes by editing labels.json (class id -> character) and run make_font.py.
"""
import json, os, sys
import numpy as np
import cv2
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pdf2kicad.primitives import load_page
from pdf2kicad import glyphs as G

THRESH_MEAN = 0.012
THRESH_MAX = 0.06


def main(pdf, out, pages):
    os.makedirs(out, exist_ok=True)
    classes = []      # dict(sig, n, w, h, where)
    for p in pages:
        pg = load_page(pdf, p - 1)
        gl = G.find_glyphs(pg.segs)
        words = G.build_words(gl)
        em0 = G.estimate_page_em(words)
        for w in words:
            em, base = G.word_metrics(w, em0)
            for g in w.glyphs:
                sig = G.glyph_signature(g, w.rot, em, base)
                best, bd = None, 9
                gw, gh = sig[:, 0].max(), sig[:, 1].max() - sig[:, 1].min()
                for c in classes:
                    if abs(c["w"] - gw) > 0.1 or abs(c["h"] - gh) > 0.1:
                        continue
                    d, dm = G.chamfer(sig, c["sig"], True)
                    if d < bd and dm <= THRESH_MAX:
                        best, bd = c, d
                if best is not None and bd <= THRESH_MEAN:
                    best["n"] += 1
                    best["where"].append((p, round(g.x0), round(g.y0)))
                else:
                    classes.append({"sig": sig, "w": gw, "h": gh, "n": 1, "em": em, "rot": w.rot, "where": [(p, round(g.x0), round(g.y0))]})
    classes.sort(key=lambda c: -c["n"])
    json.dump([{"id": i, "n": c["n"], "em": round(c["em"], 2), "rot": c["rot"], "where": c["where"][:3],
                "sig": np.round(c["sig"], 3).tolist()} for i, c in enumerate(classes)],
              open(os.path.join(out, "classes.json"), "w"))
    S, cols, rows_per = 56, 10, 8
    per = cols * rows_per
    for sheet in range((len(classes) + per - 1) // per):
        img = np.full((rows_per * (S + 30) + 10, cols * (S + 40) + 10, 3), 255, np.uint8)
        for k, c in enumerate(classes[sheet * per:(sheet + 1) * per]):
            r, cc = divmod(k, cols)
            ox, oy = 8 + cc * (S + 40), 8 + r * (S + 30)
            sig = c["sig"]
            for x, y in sig:
                px, py = int(ox + 6 + x * S * 0.9), int(oy + S - y * S * 0.9)
                cv2.circle(img, (px, py), 1, (0, 0, 0), -1)
            cv2.putText(img, "%d x%d" % (sheet * per + k, c["n"]), (ox, oy + S + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 200), 1)
        cv2.imwrite(os.path.join(out, "sheet_%d.png" % sheet), img)
    print("classes:", len(classes), " glyphs:", sum(c["n"] for c in classes))


if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2], [int(x) for x in sys.argv[3:]])
