#!/usr/bin/env python3
"""Independently re-extract connectivity from a generated .kicad_sch and diff it
against the source netlist CSV.

Does NOT import the generator: it parses the s-expressions, computes each pin's
absolute position from lib_symbols + placement, then matches labels / no-connect
flags that sit exactly on a pin end.  Also reports geometry problems
(unlabelled pins, labels on no pin, two pins on the same point, overlapping symbols).

Usage: verify_kicad_sch.py file.kicad_sch netlist.csv
"""
import csv
import sys
import argparse
from collections import defaultdict

TOL = 0.01


def tokenize(s):
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in " \t\r\n":
            i += 1
        elif c in "()":
            yield c
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n and s[j] != '"':
                if s[j] == "\\" and j + 1 < n:
                    j += 1
                buf.append(s[j])
                j += 1
            yield ("S", "".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and s[j] not in ' \t\r\n()"':
                j += 1
            yield ("A", s[i:j])
            i = j


def parse(s):
    stack, cur = [], []
    for t in tokenize(s):
        if t == "(":
            stack.append(cur)
            cur = []
        elif t == ")":
            done = cur
            cur = stack.pop()
            cur.append(done)
        else:
            cur.append(t[1])
    if stack:
        raise SystemExit("unbalanced s-expression")
    return cur[0]


def head(node):
    return node[0] if node and isinstance(node[0], str) else None


def find(node, name):
    return [c for c in node if isinstance(c, list) and head(c) == name]


def num(x):
    return float(x)


def main(sch_path, csv_path, allow_unmapped_pins=False):
    root = parse(open(sch_path).read())
    if head(root) != "kicad_sch":
        raise SystemExit("not a kicad_sch file")
    problems = []

    # lib symbol pins: lib_id -> {number: (name, x, y)}
    libs = {}
    for ls in find(root, "lib_symbols"):
        for sym in find(ls, "symbol"):
            pins = {}
            for sub in find(sym, "symbol"):
                for p in find(sub, "pin"):
                    at = find(p, "at")[0]
                    nm = find(p, "name")[0][1]
                    pn = find(p, "number")[0][1]
                    pins[pn] = (nm, num(at[1]), num(at[2]))
            libs[sym[1]] = pins

    # placed symbols -> absolute pin ends (lib y up, sheet y down, rotation 0 only)
    pin_pos = {}            # (ref, pin number) -> (x, y)
    pin_name = {}
    boxes = {}
    refs_seen = []
    for sym in find(root, "symbol"):
        lib_id = find(sym, "lib_id")[0][1]
        at = find(sym, "at")[0]
        x, y, rot = num(at[1]), num(at[2]), num(at[3])
        if rot != 0:
            problems.append("%s: rotation %s not supported by verifier" % (lib_id, rot))
        ref = [p[2] for p in find(sym, "property") if p[1] == "Reference"][0]
        refs_seen.append(ref)
        xs, ys = [x], [y]
        for p in find(sym, "pin"):
            pn = p[1]
            if pn not in libs[lib_id]:
                problems.append("%s pin %s not in lib symbol" % (ref, pn))
                continue
            nm, px, py = libs[lib_id][pn]
            pin_pos[(ref, pn)] = (x + px, y - py)
            pin_name[(ref, pn)] = nm
            xs.append(x + px)
            ys.append(y - py)
        boxes[ref] = (min(xs), min(ys), max(xs), max(ys))
    dup = {r for r in refs_seen if refs_seen.count(r) > 1}
    if dup:
        problems.append("duplicate references: %s" % sorted(dup))

    # Expected rows are parsed before pin validation so draft read-back can
    # distinguish deliberately unmapped symbol pins from broken mapped pins.
    expected = {}
    csv_rows = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as stream:
        for r in csv.DictReader(stream):
            csv_rows += 1
            ref, tok, net = r["ref"], r["pin"], r["net"]
            cands = [k for k in pin_pos if k[0] == ref and k[1] == tok]
            if not cands:
                cands = [k for k in pin_pos if k[0] == ref and pin_name[k] == tok]
            if len(cands) != 1:
                problems.append("CSV %s.%s maps to %d schematic pins" % (ref, tok, len(cands)))
                continue
            if cands[0] in expected:
                problems.append("CSV lists %s.%s twice" % (ref, tok))
            expected[cands[0]] = net

    # labels / no-connects keyed by coordinate
    at_point = defaultdict(list)
    for kind in ("label", "global_label"):
        for lb in find(root, kind):
            a = find(lb, "at")[0]
            at_point[(round(num(a[1]), 2), round(num(a[2]), 2))].append(("net", lb[1]))
    for nc in find(root, "no_connect"):
        a = find(nc, "at")[0]
        at_point[(round(num(a[1]), 2), round(num(a[2]), 2))].append(("nc", "NC"))

    got = {}   # (ref, pin number) -> net
    used_points = set()
    for key, (px, py) in pin_pos.items():
        pt = (round(px, 2), round(py, 2))
        hits = at_point.get(pt, [])
        used_points.add(pt)
        if not hits:
            if not (allow_unmapped_pins and key not in expected):
                problems.append("pin %s.%s (%s) has no label/NC at its end" % (key[0], key[1], pin_name[key]))
            continue
        kinds = {h[0] for h in hits}
        nets = {h[1] for h in hits}
        if len(nets) > 1:
            problems.append("pin %s.%s has conflicting labels %s" % (key[0], key[1], sorted(nets)))
        got[key] = "NC" if "nc" in kinds else sorted(nets)[0]
    for pt, hits in at_point.items():
        if pt not in used_points:
            problems.append("label/NC %s at %s does not touch any pin" % (hits, pt))

    # pin-end collisions between different symbols
    by_pt = defaultdict(list)
    for key, (px, py) in pin_pos.items():
        by_pt[(round(px, 2), round(py, 2))].append(key)
    for pt, ks in by_pt.items():
        if len(ks) > 1:
            problems.append("pins share one point %s: %s" % (pt, ks))

    # symbol body overlap (bounding boxes of pin extents, loose)
    refs = list(boxes)
    for i in range(len(refs)):
        for j in range(i + 1, len(refs)):
            a, b = boxes[refs[i]], boxes[refs[j]]
            if a[0] < b[2] - TOL and b[0] < a[2] - TOL and a[1] < b[3] - TOL and b[1] < a[3] - TOL:
                problems.append("symbols %s and %s overlap" % (refs[i], refs[j]))

    # Strict mode requires every physical symbol pin to be represented.
    # Draft mode leaves unrepresented pins electrically open; it never invents NC.
    if not allow_unmapped_pins:
        for key in pin_pos:
            if key not in expected:
                problems.append("schematic pin %s.%s (%s) absent from CSV" % (key[0], key[1], pin_name[key]))

    # P0: compare the independently re-extracted KiCad assignments with the
    # expected CSV for every mapped pin. Merely finding a label at the pin end
    # is insufficient: a wrong net label must fail read-back.
    for key, expected_net in expected.items():
        actual_net = got.get(key)
        if actual_net is None:
            problems.append("CSV %s.%s expected %s but schematic has no electrical assignment" %
                            (key[0], key[1], expected_net))
        elif actual_net != expected_net:
            problems.append("CSV %s.%s net mismatch: expected %r, got %r" %
                            (key[0], key[1], expected_net, actual_net))
    for key, actual_net in got.items():
        if key not in expected:
            problems.append("schematic pin %s.%s has unexpected electrical assignment %r absent from CSV" %
                            (key[0], key[1], actual_net))

    # net summary: nets that only touch a single pin are suspicious
    members = defaultdict(list)
    for key, net in got.items():
        if net != "NC":
            members[net].append("%s.%s" % key)
    singles = sorted(n for n, m in members.items() if len(m) == 1)

    print("symbols: %d   pins: %d   csv rows: %d   nets: %d" % (
        len(refs_seen), len(pin_pos), csv_rows, len(members)))
    if singles:
        print("nets with a single pin (check against drawing): %s" % ", ".join(
            "%s(%s)" % (n, members[n][0]) for n in singles))
    if problems:
        print("\n%d PROBLEM(S):" % len(problems))
        for p in problems:
            print("  - " + p)
        sys.exit(1)
    print("OK: connectivity re-extracted from the .kicad_sch matches the CSV exactly")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("schematic")
    ap.add_argument("netlist")
    ap.add_argument("--allow-unmapped-pins", action="store_true",
                    help="draft-only: allow symbol pins absent from candidate CSV to remain unconnected")
    a = ap.parse_args()
    main(a.schematic, a.netlist, allow_unmapped_pins=a.allow_unmapped_pins)
