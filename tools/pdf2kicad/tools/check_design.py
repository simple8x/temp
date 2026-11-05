#!/usr/bin/env python3
"""Whole-design checks across page netlists + BOM PDF.

1. BOM (PDF text layer, pages given) -> {ref: (value, item no)}; refs are attached to the
   nearest item row by vertical position (ref lists wrap above/below the item number).
2. Every BOM ref must appear in exactly one page; every page ref must be in the BOM;
   page value must equal BOM value.
3. Cross-page nets: report global nets used on one page only, and near-duplicate net names.
Usage: check_design.py bom.pdf FIRST_PAGE LAST_PAGE  pageDir [pageDir ...]
       (each pageDir holds netlist.csv and parts.csv)
"""
import csv, os, re, sys
from collections import defaultdict
import pymupdf

REF = re.compile(r"^(RN|SW|[A-Z])\d+$")


def read_bom(pdf, p1, p2):
    doc = pymupdf.open(pdf)
    bom, notes = {}, []
    for pno in range(p1 - 1, p2):
        words = doc[pno].get_text("words")          # x0,y0,x1,y1,text,...
        rows = []                                    # item number rows
        for w in words:
            if re.fullmatch(r"\d{1,2}", w[4]) and w[0] < 50 and w[1] > 70 and w[1] < 740:
                rows.append((w[1], int(w[4])))
        rows.sort()
        # value column: words on the same line as number, between description and "Any/maker"
        lines = defaultdict(list)
        for w in words:
            lines[round(w[1] / 3)].append(w)
        item_val = {}
        for y, no in rows:
            ln = sorted(lines.get(round(y / 3), []), key=lambda w: w[0])
            txt = [w[4] for w in ln if 50 <= w[0] < 385]       # name + code + maker columns
            item_val[no] = txt
        # refs: tokens in the right-hand column (x > 430) that look like refs separated by commas
        for w in words:
            if w[0] < 385 or w[1] < 70 or w[1] > 740:
                continue
            for tok in re.split(r",", w[4]):
                tok = tok.strip()
                if REF.match(tok) and rows:
                    y = (w[1] + w[3]) / 2
                    no = min(rows, key=lambda r: abs(r[0] - y))[1]
                    bom.setdefault(tok, {"no": no})
        for no, txt in item_val.items():
            for ref, d in bom.items():
                if d["no"] == no and "txt" not in d:
                    d["txt"] = txt
    return bom


def norm(v):
    return re.sub(r"[\s]", "", v).lower()


def main():
    pdf, p1, p2, dirs = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4:]
    bom = read_bom(pdf, p1, p2)
    pages, net_pages, problems = {}, defaultdict(set), []
    for d in dirs:
        parts = {r["ref"]: r for r in csv.DictReader(open(os.path.join(d, "parts.csv")))}
        refs = set()
        for r in csv.DictReader(open(os.path.join(d, "netlist.csv"))):
            refs.add(r["ref"])
            if r["net"] != "NC":
                net_pages[r["net"]].add(d)
        pages[d] = (refs, parts)
    seen = defaultdict(list)
    for d, (refs, parts) in pages.items():
        for r in refs:
            seen[r].append(d)
    print("BOM refs parsed: %d   schematic refs: %d" % (len(bom), len(seen)))
    for r, ds in sorted(seen.items()):
        if len(ds) > 1:
            problems.append("ref %s appears on several pages: %s" % (r, ds))
        if r not in bom:
            problems.append("ref %s is on %s but not in the BOM" % (r, ds[0]))
    for r in sorted(bom):
        if r not in seen:
            problems.append("BOM ref %s (item %s) is not on any schematic page" % (r, bom[r]["no"]))
    mism = 0
    for d, (refs, parts) in pages.items():
        for r in refs:
            if r in bom and r in parts and "txt" in bom[r]:
                v = norm(parts[r]["value"])
                if v and not any(v == norm(t) or v in norm("".join(bom[r]["txt"])) for t in bom[r]["txt"]):
                    mism += 1
                    problems.append("value %s: schematic %r vs BOM line %r" % (r, parts[r]["value"], " ".join(bom[r]["txt"])))
    # cross-page nets
    print("\nnets shared by several pages:")
    for n, ds in sorted(net_pages.items()):
        if len(ds) > 1:
            print("  %-14s %s" % (n, ", ".join(sorted(os.path.basename(x) for x in ds))))
    print("\nglobal-looking nets used on ONE page only (check they are really off-page):")
    one = [n for n, ds in sorted(net_pages.items()) if len(ds) == 1 and re.match(r"^(ESP32_|UART_|OUT|IN|\+|GND|SHIELD|D[+-]|USB|OCOM|ICOM|VBUS)", n)]
    for n in one:
        print("  %-14s only on %s" % (n, os.path.basename(list(net_pages[n])[0])))
    print("\nnear-duplicate net names (possible typos / different names for one rail):")
    names = sorted(net_pages)
    key = lambda s: re.sub(r"[^a-z0-9]", "", s.lower().replace("3v3", "33v"))
    groups = defaultdict(list)
    for n in names:
        groups[key(n)].append(n)
    for k, g in groups.items():
        if len(g) > 1:
            print("  " + " vs ".join(g))
    print()
    if problems:
        print("%d PROBLEM(S):" % len(problems))
        for p in problems:
            print("  - " + p)
        sys.exit(1)
    print("OK: BOM and schematic agree on every reference and value")


if __name__ == "__main__":
    if len(sys.argv) < 5:
        raise SystemExit(__doc__)
    main()
