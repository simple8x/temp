#!/usr/bin/env python3
"""Generic generator: netlist CSV -> self-contained KiCad 8 .kicad_sch

Inputs (CSV with header row):
  netlist.csv  net,ref,pin[,global]      REQUIRED. optional column global=1 forces a global label. pin = pin number as in the symbol/datasheet.
                                         net "NC" -> no-connect flag.
  parts.csv    ref,value,symbol,block    optional. symbol: R C CP L D LED TVS FUSE BOX
                                         (blank = guess from ref prefix; unknown prefix = BOX)
                                         block = group title, parts of one block are laid out together.
  pins.csv     ref,pin,name,side         optional, for BOX parts. side = L or R (blank = auto split).
                                         Row order inside a side = top-to-bottom order.

Pins of a BOX part that are in netlist.csv but not in pins.csv are added automatically
(named after their number).  Two-pin symbols whose pins cannot be mapped fall back to BOX.

Output is connected by net labels placed exactly on pin ends (no wires).  A net becomes a
global label when it has a single pin (goes off-sheet), spans several blocks, starts with '+',
or is listed in --global; otherwise a local label.

Usage:
  gen_kicad_sch.py netlist.csv out.kicad_sch [--parts parts.csv] [--pins pins.csv]
                   [--title TITLE] [--company TEXT] [--global NET [NET ...]]
"""
import argparse
import csv
import re
import sys
import uuid
from collections import OrderedDict, defaultdict

NS = uuid.UUID("6f1c5e0a-3b7d-4a55-9d1e-0c2f4b8a9e11")
GRID = 2.54
FONT = "(effects (font (size 1.27 1.27)))"
FONT_HIDE = "(effects (font (size 1.27 1.27)) hide)"
STROKE = "(stroke (width 0.254) (type default))"
STROKE_T = "(stroke (width 0.381) (type default))"
LIBNAME = "ModuleIO"
PROJECT = "Module_IO"
TWO_PIN = {"R", "C", "CP", "L", "D", "LED", "TVS", "FUSE"}
PREFIX_SYMBOL = {"R": "R", "C": "C", "L": "L", "D": "D", "F": "FUSE"}
REF_LETTER = {"FUSE": "F", "LED": "D", "TVS": "D", "CP": "C"}


def uid(key):
    return str(uuid.uuid5(NS, key))


def fmt(v):
    s = ("%.4f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def natkey(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


# ----------------------------------------------------------------- symbols
class Sym:
    def __init__(self, name, text, pins, halfw, top, bot):
        self.name, self.text, self.pins = name, text, pins   # pins: num -> (name, x, y, side)
        self.halfw, self.top, self.bot = halfw, top, bot


def pin_txt(num, name, x, y, ang, length):
    return ("(pin passive line (at %s %s %d) (length %s) (name \"%s\" %s) "
            "(number \"%s\" %s))" % (fmt(x), fmt(y), ang, fmt(length), esc(name), FONT, esc(num), FONT))


def head(name, ref_letter, hide_names):
    pn = "(pin_numbers hide)" if hide_names else ""
    pnm = "(pin_names (offset 0) hide)" if hide_names else "(pin_names (offset 1.016))"
    return ("(symbol \"%s:%s\" %s %s (exclude_from_sim no) (in_bom yes) (on_board yes)\n"
            "  (property \"Reference\" \"%s\" (at 0 -2.54 0) %s)\n"
            "  (property \"Value\" \"%s\" (at 0 2.54 0) %s)\n"
            "  (property \"Footprint\" \"\" (at 0 0 0) %s)\n"
            "  (property \"Datasheet\" \"\" (at 0 0 0) %s)\n"
            % (LIBNAME, esc(name), pn, pnm, ref_letter, FONT, esc(name), FONT, FONT_HIDE, FONT_HIDE))


def two_pin(name, ref_letter, gfx, p1, p2, top, bot):
    body = "  (symbol \"%s_0_1\"\n%s  )\n" % (name, gfx)
    pins = ("  (symbol \"%s_1_1\"\n    %s\n    %s\n  )\n" % (
        name, pin_txt(p1[0], p1[1], -3.81, 0, 0, 1.27), pin_txt(p2[0], p2[1], 3.81, 0, 180, 1.27)))
    text = head(name, ref_letter, True) + body + pins + ")"
    return Sym(name, text, {p1[0]: (p1[1], -3.81, 0, "L"), p2[0]: (p2[1], 3.81, 0, "R")},
               3.81, top, bot)


def poly(pts, fill="none", stroke=STROKE):
    p = " ".join("(xy %s %s)" % (fmt(x), fmt(y)) for x, y in pts)
    return "    (polyline (pts %s) %s (fill (type %s)))\n" % (p, stroke, fill)


def two_pin_symbols():
    S = {}
    rect = "    (rectangle (start -2.54 -1.016) (end 2.54 1.016) %s (fill (type none)))\n" % STROKE
    S["R"] = two_pin("R", "R", rect, ("1", "1"), ("2", "2"), 1.016, 1.016)
    cap = (poly([(-2.54, 0), (-0.508, 0)]) + poly([(0.508, 0), (2.54, 0)]) +
           poly([(-0.508, -1.524), (-0.508, 1.524)], stroke=STROKE_T) +
           poly([(0.508, -1.524), (0.508, 1.524)], stroke=STROKE_T))
    S["C"] = two_pin("C", "C", cap, ("1", "1"), ("2", "2"), 1.524, 1.524)
    S["CP"] = two_pin("CP", "C", cap + "    (text \"+\" (at -2.0 1.9 0) %s)\n" % FONT,
                      ("1", "+"), ("2", "-"), 2.2, 1.524)
    tri = (poly([(-2.54, 0), (-1.27, 0)]) + poly([(1.27, 0), (2.54, 0)]) +
           poly([(1.27, 1.27), (1.27, -1.27), (-1.27, 0), (1.27, 1.27)], "outline") +
           poly([(-1.27, 1.27), (-1.27, -1.27)]))
    S["D"] = two_pin("D", "D", tri, ("1", "K"), ("2", "A"), 1.27, 1.27)       # pin1=K, pin2=A
    S["LED"] = two_pin("LED", "D", tri + poly([(-0.635, 1.905), (0.635, 3.175)]) +
                       poly([(0.0, 1.27), (1.27, 2.54)]), ("1", "K"), ("2", "A"), 3.2, 1.27)
    tvs = (poly([(-2.54, 0), (-1.905, 0)]) + poly([(1.905, 0), (2.54, 0)]) +
           poly([(0, 1.27), (0, -1.27), (-1.905, 0), (0, 1.27)], "outline") +
           poly([(0, 1.27), (0, -1.27), (1.905, 0), (0, 1.27)], "outline") +
           poly([(-1.905, 1.27), (-1.905, -1.27)]) + poly([(1.905, 1.27), (1.905, -1.27)]))
    S["TVS"] = two_pin("TVS", "D", tvs, ("1", "1"), ("2", "2"), 1.27, 1.27)
    arcs = ""
    for i in range(4):
        a = -2.54 + 1.27 * i
        arcs += ("    (arc (start %s 0) (mid %s 0.635) (end %s 0) %s (fill (type none)))\n"
                 % (fmt(a), fmt(a + 0.635), fmt(a + 1.27), STROKE))
    S["L"] = two_pin("L", "L", arcs, ("1", "1"), ("2", "2"), 0.635, 0.2)
    S["FUSE"] = two_pin("FUSE", "F", rect + poly([(-2.54, 0), (2.54, 0)]),
                        ("1", "1"), ("2", "2"), 1.016, 1.016)
    return S


def box_symbol(name, ref_letter, left, right, width=None):
    """left/right: lists of (number, name).  Returns Sym."""
    rows = max(len(left), len(right), 1)
    h = rows * GRID
    longest = max([len(n) for _, n in left] + [0]) + max([len(n) for _, n in right] + [0])
    w = width or max(10.16, GRID * int((longest * 1.4 + 6) / GRID + 1))
    pins, ptxt = {}, []
    for i, (num, nm) in enumerate(left):
        y, x = h / 2 - 1.27 - i * GRID, -(w / 2 + GRID)
        pins[num] = (nm, x, y, "L")
        ptxt.append(pin_txt(num, nm, x, y, 0, GRID))
    for i, (num, nm) in enumerate(right):
        y, x = h / 2 - 1.27 - i * GRID, w / 2 + GRID
        pins[num] = (nm, x, y, "R")
        ptxt.append(pin_txt(num, nm, x, y, 180, GRID))
    body = ("  (symbol \"%s_0_1\"\n    (rectangle (start %s %s) (end %s %s) %s "
            "(fill (type background)))\n  )\n" % (esc(name), fmt(-w / 2), fmt(h / 2),
                                                  fmt(w / 2), fmt(-h / 2), STROKE))
    text = (head(name, ref_letter, False)
            .replace("(at 0 -2.54 0)", "(at 0 %s 0)" % fmt(h / 2 + 1.27))
            .replace("(at 0 2.54 0)", "(at 0 %s 0)" % fmt(-h / 2 - 1.27)) + body +
            "  (symbol \"%s_1_1\"\n    %s\n  )\n)" % (esc(name), "\n    ".join(ptxt)))
    return Sym(name, text, pins, w / 2 + GRID, h / 2, h / 2)


# ------------------------------------------------------------------- input
def read_csv(path, required):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        cols = [c.strip() for c in (rd.fieldnames or [])]
        miss = [c for c in required if c not in cols]
        if miss:
            raise SystemExit("%s: missing column(s) %s (has %s)" % (path, miss, cols))
        return [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in rd]


def load_inputs(a):
    nets, forced = OrderedDict(), set()
    for r in read_csv(a.netlist, ["net", "ref", "pin"]):
        if r.get("global", "").lower() in ("1", "yes", "true", "y"):
            forced.add(r["net"])
        if not (r["net"] and r["ref"] and r["pin"]):
            raise SystemExit("%s: empty net/ref/pin in row %s" % (a.netlist, r))
        d = nets.setdefault(r["ref"], OrderedDict())
        if r["pin"] in d:
            raise SystemExit("%s: %s.%s listed twice" % (a.netlist, r["ref"], r["pin"]))
        d[r["pin"]] = r["net"]
    parts = {}
    if a.parts:
        for r in read_csv(a.parts, ["ref"]):
            parts[r["ref"]] = r
        extra = set(parts) - set(nets)
        if extra:
            print("warning: parts.csv refs not in netlist (ignored): %s" % sorted(extra), file=sys.stderr)
    pinmap = defaultdict(list)
    if a.pins:
        for r in read_csv(a.pins, ["ref", "pin"]):
            pinmap[r["ref"]].append((r["pin"], r.get("name") or r["pin"], (r.get("side") or "").upper()))
    return nets, parts, pinmap, forced


def make_symbol(ref, tokens, part, pinmap, two):
    """Return (Sym, pin_token_to_number dict)."""
    want = (part.get("symbol") or "").upper() or PREFIX_SYMBOL.get(
        re.match(r"[A-Za-z]*", ref).group(0).upper(), "BOX")
    if want == "C" and ("+" in tokens or "-" in tokens):
        want = "CP"
    if want in TWO_PIN and len(tokens) <= 2:
        sym = two[want]
        m, ok = {}, True
        for t in tokens:
            hit = [n for n, v in sym.pins.items() if n == t or v[0] == t]
            if len(hit) == 1:
                m[t] = hit[0]
            else:
                ok = False
        if ok and len(set(m.values())) == len(m):
            return sym, m
        print("warning: %s pins %s do not fit symbol %s -> using BOX" % (ref, tokens, want),
              file=sys.stderr)
    # BOX
    declared = pinmap.get(ref, [])
    known = {p for p, _, _ in declared}
    names = defaultdict(int)
    for _, n, _ in declared:
        names[n] += 1
    # a netlist token may be a declared pin NAME (unique) instead of its number
    known |= {t for t in tokens if names.get(t, 0) == 1}
    rows = list(declared) + [(t, t, "") for t in sorted(tokens, key=natkey) if t not in known]
    left = [(p, n) for p, n, s in rows if s == "L"]
    right = [(p, n) for p, n, s in rows if s == "R"]
    auto = [(p, n) for p, n, s in rows if s not in ("L", "R")]
    if auto:
        auto.sort(key=lambda x: natkey(x[0]))
        half = (len(auto) + 1) // 2 if not left and not right else None
        if half is not None:
            left += auto[:half]
            right += auto[half:]
        else:   # balance the shorter side
            for item in auto:
                (left if len(left) <= len(right) else right).append(item)
    name = "%s_%s" % (re.sub(r"[^A-Za-z0-9_.+-]", "_", part.get("value") or "BOX"), ref)
    letter = re.match(r"[A-Za-z]+", ref)
    sym = box_symbol(name, (letter.group(0)[0] if letter else "U"), left, right)
    m = {}
    for t in tokens:
        hit = [n for n, v in sym.pins.items() if n == t or v[0] == t]
        if len(hit) != 1:
            raise SystemExit("%s: pin %r not found on symbol (has %s)" % (ref, t, list(sym.pins)))
        m[t] = hit[0]
    return sym, m


# ------------------------------------------------------------------- main
def text_w(s):
    return GRID * int((len(s) * 1.3) / GRID + 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("netlist")
    ap.add_argument("out")
    ap.add_argument("--parts")
    ap.add_argument("--pins")
    ap.add_argument("--title", default="Schematic")
    ap.add_argument("--company", default="")
    ap.add_argument("--instance-path", default="", help="symbol instance path, e.g. /ROOT_UUID/SHEET_UUID when used as a sub-sheet")
    ap.add_argument("--page", default="1")
    ap.add_argument("--global", dest="globals", nargs="*", default=[])
    a = ap.parse_args()

    nets, parts, pinmap, forced = load_inputs(a)
    two = two_pin_symbols()
    symof, pinnum = {}, {}
    for ref, pins in nets.items():
        symof[ref], pinnum[ref] = make_symbol(ref, list(pins), parts.get(ref, {}), pinmap, two)

    # blocks keep first-seen order; parts with no block share "Sheet"
    blocks = OrderedDict()
    for ref in nets:
        blocks.setdefault(parts.get(ref, {}).get("block") or "Sheet", []).append(ref)

    # which nets are global
    net_blocks, net_pins = defaultdict(set), defaultdict(int)
    for b, refs in blocks.items():
        for ref in refs:
            for net in nets[ref].values():
                net_blocks[net].add(b)
                net_pins[net] += 1
    gset = set(a.globals) | forced

    def is_global(net):
        return (net in gset or net.startswith("+") or net_pins[net] == 1 or len(net_blocks[net]) > 1
                or re.search(r"GND|VBUS|VCC|VDD", net, re.I) is not None)

    margin_x, y_cursor, max_x = 25.4, 38.1, 560.0
    items, titles = [], []
    for title, refs in blocks.items():
        titles.append((title, margin_x, y_cursor))
        y_cursor += 7.62
        row, cursor, rows = [], margin_x, []
        for ref in refs:
            sym = symof[ref]
            lw = max([text_w(nets[ref][t]) for t in nets[ref] if sym.pins[pinnum[ref][t]][3] == "L"] or [0])
            rw = max([text_w(nets[ref][t]) for t in nets[ref] if sym.pins[pinnum[ref][t]][3] == "R"] or [0])
            cell = GRID * int((lw + 2 * sym.halfw + rw + 7.62) / GRID + 1)
            if cursor + cell > max_x and row:
                rows.append(row)
                row, cursor = [], margin_x
            row.append((ref, sym, GRID * round((cursor + lw + sym.halfw) / GRID)))
            cursor += cell
        rows.append(row)
        for row in rows:
            h = max(s.top + s.bot for _, s, _ in row)
            rh = GRID * int((h + 20.32) / GRID + 1)
            cy = GRID * round((y_cursor + rh / 2) / GRID)
            items += [(ref, sym, cx, cy) for ref, sym, cx in row]
            y_cursor += rh
        y_cursor += 12.7

    width = max(cx + s.halfw + 40 for _, s, cx, _ in items)
    height = y_cursor + 10
    paper = next((n for n, w, h in [("A4", 297, 210), ("A3", 420, 297), ("A2", 594, 420),
                                    ("A1", 841, 594)] if width <= w and height <= h), "A1")

    root = uid("root" + a.netlist)
    out = ["(kicad_sch (version 20231120) (generator \"eeschema\") (generator_version \"8.0\")",
           " (uuid \"%s\")" % root, " (paper \"%s\")" % paper,
           " (title_block (title \"%s\") (company \"%s\") "
           "(comment 1 \"Rebuilt from PDF - connectivity via net labels\"))" % (esc(a.title), esc(a.company)),
           " (lib_symbols"]
    done = set()
    for _, sym, _, _ in items:
        if sym.name not in done:
            done.add(sym.name)
            out.append("\n".join("  " + ln for ln in sym.text.split("\n")))
    out.append(" )")
    for title, x, y in titles:
        out.append(" (text \"%s\" (at %s %s 0) (effects (font (size 2.54 2.54) bold) "
                   "(justify left bottom)) (uuid \"%s\"))" % (esc(title), fmt(x), fmt(y), uid("t" + title)))

    for ref, sym, cx, cy in items:
        value = parts.get(ref, {}).get("value", "")
        out.append(" (symbol (lib_id \"%s:%s\") (at %s %s 0) (unit 1) (exclude_from_sim no) "
                   "(in_bom yes) (on_board yes) (dnp no) (uuid \"%s\")" % (
                       LIBNAME, esc(sym.name), fmt(cx), fmt(cy), uid("sym" + ref)))
        out.append("  (property \"Reference\" \"%s\" (at %s %s 0) %s)" % (
            esc(ref), fmt(cx), fmt(cy - sym.top - 1.27), FONT))
        out.append("  (property \"Value\" \"%s\" (at %s %s 0) %s)" % (
            esc(value), fmt(cx), fmt(cy + sym.bot + 1.27), FONT))
        out.append("  (property \"Footprint\" \"\" (at %s %s 0) %s)" % (fmt(cx), fmt(cy), FONT_HIDE))
        out.append("  (property \"Datasheet\" \"\" (at %s %s 0) %s)" % (fmt(cx), fmt(cy), FONT_HIDE))
        for num in sym.pins:
            out.append("  (pin \"%s\" (uuid \"%s\"))" % (esc(num), uid("pin%s.%s" % (ref, num))))
        out.append("  (instances (project \"%s\" (path \"%s\" (reference \"%s\") (unit 1))))"
                   % (PROJECT, a.instance_path or "/" + root, esc(ref)))
        out.append(" )")
        for token, net in nets[ref].items():
            num = pinnum[ref][token]
            _, px, py, side = sym.pins[num]
            ax, ay = cx + px, cy - py
            key = "%s.%s" % (ref, num)
            ang, just = (180, "right") if side == "L" else (0, "left")
            if net == "NC":
                out.append(" (no_connect (at %s %s) (uuid \"%s\"))" % (fmt(ax), fmt(ay), uid("nc" + key)))
            elif is_global(net):
                out.append(" (global_label \"%s\" (shape bidirectional) (at %s %s %d) "
                           "(effects (font (size 1.27 1.27)) (justify %s)) (uuid \"%s\")\n"
                           "  (property \"Intersheetrefs\" \"${INTERSHEET_REFS}\" (at %s %s 0) %s))"
                           % (esc(net), fmt(ax), fmt(ay), ang, just, uid("gl" + key),
                              fmt(ax), fmt(ay), FONT_HIDE))
            else:
                out.append(" (label \"%s\" (at %s %s %d) (effects (font (size 1.27 1.27)) "
                           "(justify %s bottom)) (uuid \"%s\"))" % (esc(net), fmt(ax), fmt(ay), ang,
                                                                    just, uid("lb" + key)))
    out.append(" (sheet_instances (path \"/\" (page \"%s\")))" % a.page)
    out.append(")")
    with open(a.out, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("wrote %s: %d symbols, %d pins, paper %s (%.0f x %.0f mm needed)" % (
        a.out, len(items), sum(len(n) for n in nets.values()), paper, width, height))


if __name__ == "__main__":
    main()
