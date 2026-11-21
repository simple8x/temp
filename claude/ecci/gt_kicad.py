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


def parse_netlist_checked(path: str) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], list[str]]]:
    """(ref, pin) -> net name, plus the nodes the netlist lists under MORE than one net (CONFLICT).
    A conflicting node is NOT in the map: last-wins would pick a net without evidence. Power refs ('#') skipped."""
    root = ET.parse(path).getroot()
    seen: dict[tuple[str, str], set[str]] = {}
    for net in root.iter("net"):
        name = net.get("name") or f"net{net.get('code')}"
        for node in net.iter("node"):
            ref, pin = node.get("ref"), node.get("pin")
            if ref and pin is not None and not ref.startswith("#"):
                seen.setdefault((ref, pin), set()).add(name)
    out = {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}
    conflicts = {k: sorted(v) for k, v in seen.items() if len(v) > 1}
    return out, conflicts


def parse_netlist_xml(path: str) -> dict[tuple[str, str], str]:
    """(ref, pin) -> net name for nodes with exactly one net. Power-symbol references ('#...') are skipped."""
    return parse_netlist_checked(path)[0]


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


def read_sch(path: str, instance_path: str | None = None) -> dict:
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
                if rn is not None and len(rn) > 1 and (instance_path is None or str(pa[1]) == instance_path):
                    inst_refs.append(str(rn[1]))
        multi_inst = len(set(inst_refs)) > 1
        if len(set(inst_refs)) == 1:
            ref = inst_refs[0]
        elif instance_path is not None:          # no record for this instance: the reference is unknown
            ref, multi_inst = None, True
        mir = child(sym, "mirror")
        unit = child(sym, "unit")
        conv = child(sym, "convert")
        ln = child(sym, "lib_name")
        un = child(sym, "uuid")
        bs = child(sym, "body_style")
        placed.append({"uuid": str(un[1]) if un is not None and len(un) > 1 else None,
                       # KiCad >= 20250827 may write body_style; this reader only knows convert: fail closed
                       "body_style_unhandled": bs is not None and conv is None,"lib_id": str(lib_id_n[1]), "lib_name": str(ln[1]) if ln else None,
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
    junctions = []
    for j in children(tree, "junction"):
        a = child(j, "at")
        if a is not None and len(a) > 2:
            junctions.append((num(a[1]), num(a[2])))
    return {"libs": libs, "placed": placed, "wires": wires, "junctions": junctions, "tree": tree}


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


SAME_POINT_MM = 1e-3      # KiCad schematic IU is 100 nm; 1 um absorbs float error and is far below any grid


def gt_pin_records(sch_path: str, xml_path: str, order: str | None = None,
                   instance_path: str | None = None) -> tuple[list[dict], dict]:
    """One record per library pin of every placed non-power symbol, with the claims kept apart:
    position_status (VERIFIED: placement-order invariant; INFERRED: order chosen by the wire-end vote;
    UNKNOWN: undecidable), net_status (VERIFIED: one netlist node, one net; UNKNOWN; CONFLICT) and
    consistency (CONFLICT when a coincident visible pin is on another net). `net` is set ONLY when the
    record is scoreable exactly as before plus the fail-closed repairs; nothing here is ever promoted."""
    sch = read_sch(sch_path, instance_path)
    cal = calibrate(sch)
    order = order or ("MR" if cal["MR"] >= cal["RM"] else "RM")
    nets, conflicts = parse_netlist_checked(xml_path)
    ends = {(round(x, 2), round(y, 2)) for w in sch["wires"] for x, y in w}
    recs: list[dict] = []
    n_amb_inst = n_body = 0
    for si, sp in enumerate(sch["placed"]):
        ref = sp["ref"] or ""
        if ref.startswith("#") or sp["lib_id"].startswith("power:"):
            continue
        base = {"ref": ref, "lib_id": sp["lib_id"], "unit": sp["unit"], "symbol_uuid": sp.get("uuid"),
                "symbol_index": si, "sch": str(sch_path), "instance_path": instance_path}
        sym = sch["libs"].get(sp["lib_name"] or sp["lib_id"])
        if sp.get("multi_instance"):      # same sheet instantiated several times: (ref, pin) is not one pin
            n_amb_inst += 1
            if sym is not None:
                for p in _lib_pins(sym, sp["unit"], sp["convert"]):
                    recs.append(base | {"number": p["number"], "x": None, "y": None, "hidden": p["hide"],
                                        "net": None, "position_status": "UNKNOWN", "net_status": "UNKNOWN",
                                        "reasons": ["multi_instance_reference"]})
            else:
                recs.append(base | {"number": None, "x": None, "y": None, "hidden": False, "net": None,
                                    "position_status": "UNKNOWN", "net_status": "UNKNOWN",
                                    "reasons": ["multi_instance_reference", "lib_symbol_missing"]})
            continue
        if sym is None:
            recs.append(base | {"number": None, "x": None, "y": None, "hidden": False, "net": None,
                                "position_status": "UNKNOWN", "net_status": "UNKNOWN",
                                "reasons": ["lib_symbol_missing"]})
            continue
        pdefs = list(_lib_pins(sym, sp["unit"], sp["convert"]))
        inst_order, nd = _order_for_instance(sp, pdefs, ends, order)
        if inst_order is None:           # orientation cannot be decided from the schematic itself
            n_amb_inst += 1
        body = bool(sp.get("body_style_unhandled"))
        n_body += body
        for p in pdefs:
            x, y = _place(p["x"], p["y"], sp["at"], sp["mirror"], inst_order or order)
            key = (ref, p["number"])
            reasons = []
            if inst_order is None:
                pos = "UNKNOWN"
                reasons.append("orientation_undecidable")
            elif nd:
                pos = "INFERRED"
                reasons.append("orientation_by_wire_end_vote")
            else:
                pos = "VERIFIED"
            if body:
                pos = "UNKNOWN"
                reasons.append("body_style_unhandled")
            if key in conflicts:
                ns = "CONFLICT"
                reasons.append("netlist_node_on_several_nets")
            elif key in nets:
                ns = "VERIFIED"
            else:
                ns = "UNKNOWN"
                reasons.append("node_not_in_netlist")
            net = nets.get(key) if (inst_order and not body and ns == "VERIFIED") else None
            recs.append(base | {"number": p["number"], "x": x * MM_TO_PT, "y": y * MM_TO_PT, "hidden": p["hide"],
                                "net": net, "position_status": pos, "net_status": ns, "reasons": reasons,
                                "lib_pin_at": (p["x"], p["y"])})
    pinned = [r for r in recs if r["number"] is not None and r["x"] is not None]
    # (ref, pin) must identify ONE pin; duplicated references (unannotated copies, repeated pin numbers)
    # make the netlist lookup ambiguous, so those pins carry no net and are excluded from scoring.
    cnt: dict[tuple[str, str], int] = {}
    for r in pinned:
        cnt[(r["ref"], r["number"])] = cnt.get((r["ref"], r["number"]), 0) + 1
    dup = {k for k, n in cnt.items() if n > 1}
    for r in pinned:
        if (r["ref"], r["number"]) in dup:
            r["net"] = None
            r["net_status"] = "UNKNOWN" if r["net_status"] != "CONFLICT" else "CONFLICT"
            r["reasons"].append("duplicate_ref_pin")
    # physical consistency: two visible pins at the same point are one electrical point in KiCad. If the netlist
    # puts them on different nets, the computed position of one of those symbols is wrong: drop every pin of the
    # symbols involved from scoring. "Same point" = within SAME_POINT_MM, neighbour cells included (the former
    # 0.1 pt grid split coincident pins across a cell border and merged pins up to ~0.14 pt apart).
    cs = SAME_POINT_MM * MM_TO_PT
    grid: dict[tuple[int, int], list[dict]] = {}
    for r in pinned:
        r["net_pre_consistency"] = r["net"]
    live = [r for r in pinned if r["net"] and not r["hidden"]]
    for r in live:
        grid.setdefault((math.floor(r["x"] / cs), math.floor(r["y"] / cs)), []).append(r)
    bad_refs: set[str] = set()
    for r in live:
        gx, gy = math.floor(r["x"] / cs), math.floor(r["y"] / cs)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for q in grid.get((gx + dx, gy + dy), []):
                    if q is not r and q["net"] != r["net"] and math.hypot(q["x"] - r["x"], q["y"] - r["y"]) <= cs:
                        bad_refs |= {r["ref"], q["ref"]}
    for r in pinned:
        r["consistency"] = "CONFLICT" if r["ref"] in bad_refs and r["net"] else "OK"
    for r in pinned:
        if r["consistency"] == "CONFLICT":
            r["net"] = None
            r["reasons"].append("coincident_pin_on_other_net")
    for r in recs:
        r.setdefault("consistency", "OK")
        r["status"] = claim_status(r)
    info = {"calibration": cal, "order": order, "placed": len(sch["placed"]), "wires": len(sch["wires"]),
            "ambiguous_pins": len(dup), "ambiguous_instances": n_amb_inst, "inconsistent_refs": len(bad_refs),
            "netlist_conflicts": len(conflicts), "body_style_unhandled": n_body,
            "junctions_on_wire_interior": junctions_on_wire_interior(sch)}
    return recs, info


_RANK = {"CONFLICT": 0, "UNKNOWN": 1, "INFERRED": 2, "VERIFIED": 3}


def claim_status(r: dict) -> str:
    """Weakest of the separate claims. Never stronger than any input claim (no promotion path)."""
    parts = [r["position_status"], r["net_status"], "CONFLICT" if r.get("consistency") == "CONFLICT" else "VERIFIED"]
    for s_ in parts:
        if s_ not in _RANK:
            raise ValueError(f"unknown claim state {s_!r}")
    return min(parts, key=_RANK.__getitem__)


def junctions_on_wire_interior(sch: dict) -> int:
    """Diagnostic only: junctions lying strictly inside a wire (not at an end). `wire_component_index`
    joins wires only end-to-end/T, so such a junction is a place where the local/nonlocal split may differ
    from KiCad. Reported, never used to change connectivity (KiCad semantics not verified here)."""
    n = 0
    for jx, jy in sch.get("junctions", []):
        for (ax, ay), (bx, by) in sch["wires"]:
            if math.hypot(jx - ax, jy - ay) <= SAME_POINT_MM or math.hypot(jx - bx, jy - by) <= SAME_POINT_MM:
                continue
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            if L2 == 0:
                continue
            t = ((jx - ax) * dx + (jy - ay) * dy) / L2
            if 0 < t < 1 and math.hypot(jx - (ax + t * dx), jy - (ay + t * dy)) <= SAME_POINT_MM:
                n += 1
                break
    return n


def gt_pins(sch_path: str, xml_path: str, order: str | None = None, instance_path: str | None = None) -> tuple[list[GtPin], dict]:
    recs, info = gt_pin_records(sch_path, xml_path, order, instance_path)
    pins = [GtPin(r["ref"], r["number"], r["x"], r["y"], r["net"], r["hidden"], r["lib_id"])
            for r in recs if r["number"] is not None and r["x"] is not None]
    return pins, info


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


def wire_component_index(sch_path: str):
    """Geometric connectivity of the sheet's wires alone, as a function (x_pt, y_pt) -> component id or None.

    Two wires belong together when an end of one lies on the other (end-to-end or T); crossings do not join.
    Labels, power symbols and other sheets are NOT used: this is what a reader can see in the drawing, so it
    separates pairs that are connected by wires on this page from pairs connected only by name or other pages."""
    sch = read_sch(sch_path)
    segs = [((a[0], a[1]), (b[0], b[1])) for a, b in sch["wires"]]
    n = len(segs)
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def on(p, a, b, tol=0.02):
        (px, py), (ax, ay), (bx, by) = p, a, b
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
        return (px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2 <= tol * tol

    cell: dict = {}
    for i, (a, b) in enumerate(segs):
        for cx in range(int(min(a[0], b[0]) // 5) - 1, int(max(a[0], b[0]) // 5) + 2):
            for cy in range(int(min(a[1], b[1]) // 5) - 1, int(max(a[1], b[1]) // 5) + 2):
                cell.setdefault((cx, cy), []).append(i)
    for i, (a, b) in enumerate(segs):
        for end in (a, b):
            for j in cell.get((int(end[0] // 5), int(end[1] // 5)), []):
                if j != i and on(end, *segs[j]) and find(i) != find(j):
                    parent[find(i)] = find(j)

    def comp_of(x_pt: float, y_pt: float, tol_pt: float = 0.6):
        x, y = x_pt / MM_TO_PT, y_pt / MM_TO_PT
        for j in cell.get((int(x // 5), int(y // 5)), []):
            if on((x, y), *segs[j], tol=tol_pt / MM_TO_PT):
                return find(j)
        return None

    return comp_of


def hierarchy(root_sch: str) -> list[tuple[str, Path]]:
    """Sheet instances in depth-first order from the root: [(instance_path, file), ...], root first.
    instance_path is '/<root uuid>/<sheet uuid>/...' exactly as KiCad writes it in symbol instance records."""
    root = Path(root_sch)
    out: list[tuple[str, Path]] = []

    def walk(f: Path, path: str, depth: int) -> None:
        out.append((path, f))
        if depth > 12:
            return
        tree = parse(f.read_text(encoding="utf-8", errors="replace"))
        for sh in children(tree, "sheet"):
            u = child(sh, "uuid")
            fn = None
            for pr in children(sh, "property"):
                if len(pr) > 2 and str(pr[1]) in ("Sheetfile", "Sheet file"):
                    fn = str(pr[2])
            if u is None or fn is None or not (f.parent / fn).exists():
                continue
            walk(f.parent / fn, path + "/" + str(u[1]), depth + 1)

    tree = parse(root.read_text(encoding="utf-8", errors="replace"))
    ru = child(tree, "uuid")
    walk(root, "/" + (str(ru[1]) if ru else ""), 0)
    return out


def find_root(schs: list, project_dir: str) -> Path | None:
    """The root schematic: the one named like the .kicad_pro file, else the only one nobody includes."""
    d = Path(project_dir)
    pros = sorted(d.rglob("*.kicad_pro"))
    names = {Path(x).name: Path(x) for x in schs}
    if pros and (pros[0].stem + ".kicad_sch") in names:
        return names[pros[0].stem + ".kicad_sch"]
    included = set()
    for f in schs:
        for sh in children(parse(Path(f).read_text(encoding="utf-8", errors="replace")), "sheet"):
            for pr in children(sh, "property"):
                if len(pr) > 2 and str(pr[1]) in ("Sheetfile", "Sheet file"):
                    included.add(str(pr[2]))
    roots = [Path(f) for f in schs if Path(f).name not in included]
    return roots[0] if len(roots) == 1 else None
