"""Ground truth from KiCad itself (M4.0): pin positions from a .kicad_sch, connectivity from the
netlist KiCad computed. Nothing here uses ECCI output, so it is independent of the algorithm.

Coordinate model (ASSUMPTION, calibrated on data by `calibrate`): schematic mm, y down, origin at the
page's top-left; the PDF plot is 1:1, so page points = mm * 72 / 25.4.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .sexpr import child, children, num, parse

MM_TO_PT = 72.0 / 25.4


@dataclass(frozen=True)
class GtPin:
    ref: str
    number: str
    x: float            # page points, y down
    y: float
    net: str | None     # KiCad net name, None if the netlist does not list the pin
    hidden: bool        # pin not drawn (e.g. hidden power pin): cannot be found in the PDF
    lib_id: str


def parse_netlist_xml(path: str) -> dict[tuple[str, str], str]:
    """(ref, pin number) -> net name. Power-symbol references ('#...') are skipped."""
    root = ET.parse(path).getroot()
    out: dict[tuple[str, str], str] = {}
    for net in root.iter("net"):
        name = net.get("name") or f"net{net.get('code')}"
        for node in net.iter("node"):
            ref, pin = node.get("ref"), node.get("pin")
            if ref and pin is not None and not ref.startswith("#"):
                out[(ref, pin)] = name
    return out


def _is_hidden(node: list) -> bool:
    for c in node:
        if c == "hide":
            return True
        if isinstance(c, list) and c and c[0] == "hide" and len(c) > 1 and c[1] == "yes":
            return True
    return False


def _lib_pins(sym: list, unit: int, convert: int) -> list[dict]:
    pins = []
    for sub in children(sym, "symbol"):
        parts = str(sub[1]).rsplit("_", 2)
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            u, st = int(parts[1]), int(parts[2])
        else:
            u, st = 0, 0
        if u not in (0, unit) or st not in (0, convert):
            continue
        for g in children(sub, "pin"):
            at = child(g, "at")
            num_n = child(g, "number")
            if at is None or num_n is None:
                continue
            pins.append({"number": str(num_n[1]), "x": num(at[1]), "y": num(at[2]), "hide": _is_hidden(g)})
    return pins


def _place(px: float, py: float, at: tuple[float, float, float], mirror: str | None, order: str):
    """Library (y up) -> schematic mm (y down). order 'MR' = mirror then rotate, 'RM' = rotate then mirror."""
    X, Y, rot = at
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))

    def mir(x, y):
        return (x, -y) if mirror == "x" else ((-x, y) if mirror == "y" else (x, y))

    if order == "MR":
        x, y = mir(px, py)
        qx, qy = x * c - y * s, x * s + y * c
    else:
        qx, qy = px * c - py * s, px * s + py * c
        qx, qy = mir(qx, qy)
    return X + qx, Y - qy


def read_sch(path: str) -> dict:
    tree = parse(Path(path).read_text(encoding="utf-8", errors="replace"))
    if not tree or tree[0] != "kicad_sch":
        raise ValueError("not a kicad_sch file")
    libs = {}
    ls = child(tree, "lib_symbols")
    for sym in (children(ls, "symbol") if ls else []):
        libs[str(sym[1])] = sym
    placed = []
    for sym in children(tree, "symbol"):
        lib_id_n = child(sym, "lib_id")
        at = child(sym, "at")
        if lib_id_n is None or at is None:
            continue
        ref = None
        for p in children(sym, "property"):
            if len(p) > 2 and p[1] == "Reference":
                ref = str(p[2])
        # hierarchical sheets: the annotation that the netlist uses lives in the instance record, not in the
        # Reference property (which can be stale). One instance -> use it; several -> ambiguous, excluded.
        inst_refs = []
        inst = child(sym, "instances")
        for pr in (children(inst, "project") if inst else []):
            for pa in children(pr, "path"):
                rn = child(pa, "reference")
                if rn is not None and len(rn) > 1:
                    inst_refs.append(str(rn[1]))
        multi_inst = len(set(inst_refs)) > 1
        if len(set(inst_refs)) == 1:
            ref = inst_refs[0]
        mir = child(sym, "mirror")
        unit = child(sym, "unit")
        conv = child(sym, "convert")
        ln = child(sym, "lib_name")
        placed.append({"lib_id": str(lib_id_n[1]), "lib_name": str(ln[1]) if ln else None,
                       "at": (num(at[1]), num(at[2]), num(at[3]) if len(at) > 3 else 0.0),
                       "mirror": str(mir[1]) if mir and len(mir) > 1 else None,
                       "unit": int(unit[1]) if unit else 1, "convert": int(conv[1]) if conv else 1,
                       "ref": ref, "multi_instance": multi_inst})
    wires = []
    for w in children(tree, "wire"):
        pts = child(w, "pts")
        xy = [(num(c[1]), num(c[2])) for c in children(pts, "xy")] if pts else []
        if len(xy) >= 2:
            wires.append((xy[0], xy[-1]))
    return {"libs": libs, "placed": placed, "wires": wires, "tree": tree}


def calibrate(sch: dict) -> dict[str, int]:
    """Count library pins whose computed position coincides with a wire end, for each placement order.
    The order with the larger count is the right convention; both counts are reported."""
    ends = {(round(x, 2), round(y, 2)) for w in sch["wires"] for x, y in w}
    out = {}
    for order in ("MR", "RM"):
        hit = 0
        for sp in sch["placed"]:
            sym = sch["libs"].get(sp["lib_name"] or sp["lib_id"])
            if sym is None:
                continue
            for p in _lib_pins(sym, sp["unit"], sp["convert"]):
                x, y = _place(p["x"], p["y"], sp["at"], sp["mirror"], order)
                if (round(x, 2), round(y, 2)) in ends:
                    hit += 1
        out[order] = hit
    return out


def _order_for_instance(sp: dict, pins_def: list, ends: set, default: str) -> tuple[str | None, int]:
    """Placement order for ONE instance. Where mirror+rotation make the two orders disagree, the order whose
    pin tips land on more wire ends wins; a tie means the instance is ambiguous (None). Returns (order, n_diff)."""
    def tips(order):
        return [tuple(round(v, 2) for v in _place(p["x"], p["y"], sp["at"], sp["mirror"], order)) for p in pins_def]
    a, b = tips("MR"), tips("RM")
    if a == b:
        return default, 0
    ha, hb = sum(t in ends for t in a), sum(t in ends for t in b)
    if ha == hb:
        return None, 1
    return ("MR" if ha > hb else "RM"), 1


def gt_pins(sch_path: str, xml_path: str, order: str | None = None) -> tuple[list[GtPin], dict]:
    sch = read_sch(sch_path)
    cal = calibrate(sch)
    order = order or ("MR" if cal["MR"] >= cal["RM"] else "RM")
    nets = parse_netlist_xml(xml_path)
    ends = {(round(x, 2), round(y, 2)) for w in sch["wires"] for x, y in w}
    pins: list[GtPin] = []
    n_amb_inst = 0
    for sp in sch["placed"]:
        ref = sp["ref"] or ""
        if ref.startswith("#") or sp["lib_id"].startswith("power:"):
            continue
        if sp.get("multi_instance"):      # same sheet instantiated several times: (ref, pin) is not one pin
            n_amb_inst += 1
            continue
        sym = sch["libs"].get(sp["lib_name"] or sp["lib_id"])
        if sym is None:
            continue
        pdefs = list(_lib_pins(sym, sp["unit"], sp["convert"]))
        inst_order, nd = _order_for_instance(sp, pdefs, ends, order)
        if inst_order is None:           # orientation cannot be decided from the schematic itself
            n_amb_inst += 1
        for p in pdefs:
            x, y = _place(p["x"], p["y"], sp["at"], sp["mirror"], inst_order or order)
            net = nets.get((ref, p["number"])) if inst_order else None
            pins.append(GtPin(ref, p["number"], x * MM_TO_PT, y * MM_TO_PT, net, p["hide"], sp["lib_id"]))
    # (ref, pin) must identify ONE pin; duplicated references (unannotated copies, repeated pin numbers)
    # make the netlist lookup ambiguous, so those pins carry no net and are excluded from scoring.
    seen: dict[tuple[str, str], int] = {}
    for p in pins:
        seen[(p.ref, p.number)] = seen.get((p.ref, p.number), 0) + 1
    dup = {k for k, n in seen.items() if n > 1}
    pins = [GtPin(p.ref, p.number, p.x, p.y, None if (p.ref, p.number) in dup else p.net, p.hidden, p.lib_id)
            for p in pins]
    # physical consistency: two visible pins at the same point are one electrical point in KiCad. If the netlist puts
    # them on different nets, the computed position of one of those symbols is wrong (orientation we could not decide):
    # drop every pin of the symbols involved from scoring instead of scoring against a bad reference.
    at: dict[tuple[int, int], list[int]] = {}
    for k, q in enumerate(pins):
        if q.net and not q.hidden:
            at.setdefault((round(q.x / 0.1), round(q.y / 0.1)), []).append(k)
    bad_refs: set[str] = set()
    for ks in at.values():
        if len({pins[k].net for k in ks}) > 1:
            bad_refs |= {pins[k].ref for k in ks}
    if bad_refs:
        pins = [GtPin(q.ref, q.number, q.x, q.y, None if q.ref in bad_refs else q.net, q.hidden, q.lib_id) for q in pins]
    return pins, {"calibration": cal, "order": order, "placed": len(sch["placed"]), "wires": len(sch["wires"]),
                  "ambiguous_pins": len(dup), "ambiguous_instances": n_amb_inst, "inconsistent_refs": len(bad_refs)}


def embedded_library_text(sch_path: str) -> str:
    """A .kicad_sym text made from the schematic's own lib_symbols (so symbols match what was drawn)."""
    sch = read_sch(sch_path)
    out = ["(kicad_symbol_lib (version 20241209) (generator ecci_gt)"]
    for name, sym in sorted(sch["libs"].items()):
        out.append(_dump(sym))
    out.append(")")
    return "\n".join(out)


def _dump(node) -> str:
    if isinstance(node, list):
        return "(" + " ".join(_dump(c) for c in node) + ")"
    s = str(node)
    if s == "" or any(ch in s for ch in ' ()"\t\n') :
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s
