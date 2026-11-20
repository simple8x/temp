"""M4.2 native KiCad export: `ecci.connectivity` + `ecci.symbols` -> `.kicad_sch` + manifest.

Fail-closed policy (every decision is written to the manifest with its reason):
  * A symbol is PLACED only if the symbol record says identity CONFIRMED, transform UNIQUE or EQUIVALENT,
    resolution UNIQUE with pin id_state CONFIRMED, the library holds the symbol's text, its scale matches the
    page scale, its D4 orientation has a KiCad (rotation, mirror) whose meaning does not depend on the
    mirror/rotate order, and it cannot create a connection of its own (power symbols, hidden power-input pins).
    Everything else is NOT_PLACED: ambiguous, unknown, or unsupported states are never resolved here.
  * Connectivity: only VERIFIED groups of the connectivity document (`connectivity[].verified_group`, which the
    reader recomputes from VERIFIED edges) become nets. INFERRED (site links, TR4/TR5), UNKNOWN and CONFLICT
    are listed in the manifest and NOT exported. Proximity and cluster membership never create a net.
  * A net is drawn as one local label per member pin, placed exactly on the pin end that KiCad computes from the
    library (integer arithmetic in KiCad's 100 nm units). No wire geometry is emitted: the connectivity
    document does not carry wire polylines, and a re-drawn wire could touch pins that the evidence never
    connected.
  * Designators: the source reference is UNKNOWN (schema 1 has no designator reader). KiCad needs one, so each
    placed symbol gets `ECCI<cluster>`, marked SYNTHETIC in the manifest and never presented as a PDF value.
  * Pins of placed symbols that could coincide with a pin or label of another net are detected and the
    symbols involved are withdrawn (KiCad would join them by position).

Opening in KiCad is NOT a claim of electrical correctness; see kicad_roundtrip for the checks that are made.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid

from .sexpr import child, children, parse

FORMAT = "ecci.kicad_export.manifest"
SCHEMA_VERSION = 1
SCH_VERSION = "20230121"          # KiCad 7 file format; KiCad 8/9 read it
IU_PER_MM = 10000                 # KiCad schematic internal unit: 100 nm
SCALE_REL_TOL = 5e-3
MM_TO_PT = 72.0 / 25.4
NS = uuid.UUID("6f1c3c52-6e1e-4c0a-9a8e-ecc1ecc1ecc1")

PLACED, NOT_PLACED = "PLACED", "NOT_PLACED"
SYNTHETIC = "SYNTHETIC"


class ExportError(ValueError):
    pass


# ------------------------------------------------------------------------------------------- orientation
def _matmul(a, b):
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3], a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3])


_ROT = {0: (1, 0, 0, 1), 90: (0, -1, 1, 0), 180: (-1, 0, 0, -1), 270: (0, 1, -1, 0)}
_MIR = {None: (1, 0, 0, 1), "x": (1, 0, 0, -1), "y": (-1, 0, 0, 1)}
_FLIP = (1, 0, 0, -1)          # library y up -> schematic y down


def kicad_matrix(rot: int, mirror: str | None, order: str = "MR"):
    """Library (y up) -> schematic (y down) linear part for KiCad's (at x y rot) (mirror ...), in the same two
    conventions that gt_kicad._place distinguishes: MR = mirror then rotate, RM = rotate then mirror."""
    r, m = _ROT[rot], _MIR[mirror]
    lin = _matmul(r, m) if order == "MR" else _matmul(m, r)
    return _matmul(_FLIP, lin)


# The mirror/rotate order only matters for the two diagonal reflections. It is NOT assumed: None refuses them;
# "MR"/"RM" may be set only after the real kicad-cli confirmed it (tests/kicad/test_kicad_cli.py checks both
# that the chosen order round-trips and that the other one does not).
MIRROR_ORDER: str | None = "RM"     # measured with kicad-cli 7.0.11 (MR splits nets); re-checked in CI


def orientation_for(matrix, order: str | None = None) -> tuple[int, str | None] | None:
    """(rot, mirror) whose page matrix is `matrix`, preferring no mirror. Without `order`, only combinations
    whose meaning is the same under both conventions; None if there is none (diagonal reflections)."""
    m = tuple(int(v) for v in matrix)
    for mir in (None, "x", "y"):
        for rot in (0, 90, 180, 270):
            if order is None:
                if kicad_matrix(rot, mir, "MR") == m and kicad_matrix(rot, mir, "RM") == m:
                    return rot, mir
            elif kicad_matrix(rot, mir, order) == m:
                return rot, mir
    return None


# ------------------------------------------------------------------------------------------- library text
def _scan_top_symbols(text: str) -> dict[str, tuple[int, int]]:
    """name -> (start, end) of every top-level `(symbol "name" ...)` of a kicad_symbol_lib, by bracket scan
    that respects quoted strings. The exact library text is embedded, not a re-serialization."""
    out: dict[str, tuple[int, int]] = {}
    depth, i, n, start = 0, 0, len(text), None
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif ch == "(":
            depth += 1
            if depth == 2:
                start = i
        elif ch == ")":
            if depth == 2 and start is not None:
                node = text[start:i + 1]
                head = node[1:].lstrip()
                if head.startswith("symbol"):
                    tree = parse(node)
                    out[str(tree[1])] = (start, i + 1)
                start = None
            depth -= 1
        i += 1
    return out


def raw_library_from_schematic(sch_text: str) -> str:
    """A kicad_symbol_lib holding the EXACT text of a schematic's lib_symbols children (no re-serialization)."""
    depth, i, n = 0, 0, len(sch_text)
    lib_start = lib_end = None
    while i < n:
        ch = sch_text[i]
        if ch == '"':
            i += 1
            while i < n and sch_text[i] != '"':
                i += 2 if sch_text[i] == "\\" else 1
        elif ch == "(":
            depth += 1
            if depth == 2 and sch_text[i + 1:].lstrip().startswith("lib_symbols"):
                lib_start = i
        elif ch == ")":
            if depth == 2 and lib_start is not None and lib_end is None:
                lib_end = i
            depth -= 1
        i += 1
    if lib_start is None or lib_end is None:
        raise ExportError("schematic has no lib_symbols")
    body = sch_text[lib_start + 1:lib_end].lstrip()[len("lib_symbols"):]
    return "(kicad_symbol_lib (version 20230121) (generator ecci)\n" + body + "\n)\n"


def _quote(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def library_symbol_text(lib_text: str, name: str, lib_id: str) -> str:
    spans = _scan_top_symbols(lib_text)
    if name not in spans:
        raise ExportError(f"library has no symbol {name!r}")
    a, b = spans[name]
    node = lib_text[a:b]
    k = node.index("symbol") + len("symbol")
    rest = node[k:].lstrip()
    if rest.startswith('"'):
        j = 1
        while rest[j] != '"':
            j += 2 if rest[j] == "\\" else 1
        tail = rest[j + 1:]
    else:
        j = 0
        while rest[j] not in " \t\r\n()":
            j += 1
        tail = rest[j:]
    return "(symbol " + _quote(lib_id) + tail


def implicit_connection_reasons(sym_node: list) -> list[str]:
    """Library features that make KiCad connect a pin without any drawn connection."""
    out = []
    if child(sym_node, "power") is not None:
        out.append("power symbol (KiCad joins power symbols of one value into a global net)")
    for sub in children(sym_node, "symbol"):
        for pin in children(sub, "pin"):
            typ = str(pin[1]) if len(pin) > 1 else ""
            hidden = any(x == "hide" for x in pin) or any(isinstance(x, list) and x and x[0] == "hide" and
                                                         (len(x) < 2 or str(x[1]) == "yes") for x in pin)
            if typ == "power_in" and hidden:
                num = child(pin, "number")
                out.append(f"hidden power_in pin {num[1] if num else '?'} (KiCad joins it to a global net by name)")
    return out


# ------------------------------------------------------------------------------------------- planning
def _iu(v: float) -> int:
    return int(round(v * IU_PER_MM))


def _split_unit(name: str) -> tuple[str, int]:
    if "#u" in name:
        base, u = name.rsplit("#u", 1)
        if u.isdigit():
            return base, int(u)
    return name, 1


def _lib_id(base: str) -> str:
    return base if ":" in base else f"ecci:{base}"


def _uuid(seed: str) -> str:
    return str(uuid.uuid5(NS, seed))


def _lib_pin_iu(sym_node: list, unit: int) -> dict[str, tuple[int, int]]:
    """pin number -> library pin end in IU (y up), units 0 and `unit`, body style 1, as KiCad instantiates."""
    out: dict[str, tuple[int, int]] = {}
    for sub in children(sym_node, "symbol"):
        parts = str(sub[1]).rsplit("_", 2)
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            u, style = int(parts[1]), int(parts[2])
        else:
            u, style = 0, 0
        if style >= 2 or u not in (0, unit):
            continue
        for pin in children(sub, "pin"):
            at = child(pin, "at")
            num = child(pin, "number")
            if at is None or num is None:
                continue
            out.setdefault(str(num[1]), (_iu(float(at[1])), _iu(float(at[2]))))
    return out


def withdraw_coincident(decisions: list[dict]) -> None:
    """Withdraw (NOT_PLACED) every placed symbol with a pin end on the same KiCad point as a pin of another
    net or of no net: KiCad joins coincident pin ends, so placing both would create an unverified connection."""
    while True:
        at_pos: dict[tuple, set] = {}
        for d in decisions:
            if d["decision"] != PLACED:
                continue
            for p in d["pins"]:
                at_pos.setdefault(tuple(p["kicad_iu"]), set()).add((d["cluster"], p["object"], p["verified_group"]))
        bad = set()
        for pos, occ in at_pos.items():
            groups = {g if g is not None else ("solo", o) for _, o, g in occ}
            if len(occ) > 1 and len(groups) > 1:
                bad |= {cid for cid, _, _ in occ}
        if not bad:
            return
        for d in decisions:
            if d["cluster"] in bad and d["decision"] == PLACED:
                d["decision"] = NOT_PLACED
                d["reasons"].append("a pin end coincides with a pin of another net (KiCad would connect them)")
                for key in ("lib_symbol", "lib_id", "lib_base", "unit", "at_iu", "rot", "mirror",
                            "transform_choice", "synthetic_reference"):
                    d.pop(key, None)
                d["pins"] = []


def plan(conn: dict, syms: dict, lib_text: str, stem: str = "ecci_export") -> dict:
    """Pure decision step. Both documents must already have passed their readers (writer.loads and
    symrec.loads); this function re-checks the binding and refuses rather than guess."""
    if syms.get("connectivity_sha256") != conn.get("content_sha256"):
        raise ExportError("symbol document is not bound to this connectivity document")
    lib_sha = hashlib.sha256(lib_text.encode("utf-8")).hexdigest()
    if syms["library"]["sha256"] != lib_sha:
        raise ExportError("library text does not match the library hash of the symbol document")
    lib_tree = parse(lib_text)
    lib_nodes = {str(n[1]): n for n in children(lib_tree, "symbol")}
    spans = set(_scan_top_symbols(lib_text))
    vgroup = {c["object"]: c["verified_group"] for c in conn["connectivity"]}
    quarantined = {o for grp, _ in conn["quarantined"] for o in grp}

    # page scale: median of the scales of the clusters that would otherwise be placeable
    scales = sorted(c["candidates"][[i for i, x in enumerate(c["candidates"]) if x["selected"]][0]]["scale"]
                    for c in syms["clusters"] if c["transform"]["state"] in ("UNIQUE", "EQUIVALENT"))
    k = scales[len(scales) // 2] if scales else MM_TO_PT

    decisions = []
    for c in syms["clusters"]:
        d = {"cluster": c["id"], "decision": NOT_PLACED, "reasons": [], "identity": c["identity"],
             "transform": c["transform"], "resolution": c["resolution"],
             "source_reference": c["reference"], "source_value": c["value"],
             "candidates": [{"symbol": x["symbol"], "orient": x["orient"], "scale": x["scale"], "tx": x["tx"],
                             "ty": x["ty"], "residual": x["residual"], "selected": x["selected"]}
                            for x in c["candidates"]],
             "primitives": c["primitives"], "pins": []}
        R = d["reasons"]
        if c["identity"]["state"] != "CONFIRMED":
            R.append(f"identity {c['identity']['state']} ({len(c['identity']['names'])} symbol names)")
        if c["transform"]["state"] not in ("UNIQUE", "EQUIVALENT"):
            R.append(f"transform {c['transform']['state']} ({c['transform']['n_candidates']} candidates)")
        if c["resolution"]["status"] != "UNIQUE" or c["resolution"]["id_state"] != "CONFIRMED":
            R.append(f"resolution {c['resolution']['status']}/{c['resolution']['id_state']}")
        if not c["pins"]:
            R.append("no pins")
        if R:
            decisions.append(d)
            continue
        sel = [x for x in c["candidates"] if x["selected"]]
        ci = c["transform"]["candidate"]
        if c["transform"]["state"] == "EQUIVALENT":
            # every selected candidate puts every pin number on the same tip; take the first one that KiCad can
            # express without the mirror/rotate-order question. This is a choice and the manifest says so.
            ci = next((i for i, x in enumerate(sel) if orientation_for(x["matrix"], MIRROR_ORDER) is not None), ci)
        cand = sel[ci]
        base, unit = _split_unit(cand["symbol"])
        if base not in lib_nodes or base not in spans:
            R.append(f"library text has no top-level symbol {base!r}")
        elif implicit_connection_reasons(lib_nodes[base]):
            R.extend(implicit_connection_reasons(lib_nodes[base]))
        if abs(cand["scale"] / k - 1.0) > SCALE_REL_TOL:
            R.append(f"scale {cand['scale']:.6g} differs from page scale {k:.6g}")
        ori = orientation_for(cand["matrix"], MIRROR_ORDER)
        if ori is None:
            R.append(f"orientation {cand['matrix']} has no KiCad (rot, mirror) independent of the mirror/rotate order")
        if R:
            decisions.append(d)
            continue
        lib_pins = _lib_pin_iu(lib_nodes[base], unit)
        if sorted(lib_pins) != sorted(p["number"] for p in cand["pins"]):
            R.append("library pins of the unit differ from the template pins")
            decisions.append(d)
            continue
        rot, mir = ori
        T = kicad_matrix(rot, mir, MIRROR_ORDER or "MR")      # order-independent unless MIRROR_ORDER is set
        at = (_iu(cand["tx"] / k), _iu(cand["ty"] / k))
        tol_iu = 0.01 * IU_PER_MM            # 10 um: the tip and the KiCad pin end come from the same template
        pins = []
        for p in c["pins"]:
            ends = set()
            for num in p["numbers"]:
                lx, ly = lib_pins[num]
                ends.add((at[0] + T[0] * lx + T[1] * ly, at[1] + T[2] * lx + T[3] * ly))
            exp = (_iu(p["tip"][0] / k), _iu(p["tip"][1] / k))
            if len(ends) != 1:
                R.append(f"pin {p['object']}: stacked numbers {p['numbers']} are not one KiCad pin position")
                break
            end = next(iter(ends))
            if math.hypot(end[0] - exp[0], end[1] - exp[1]) > tol_iu:
                R.append(f"pin {p['object']}: KiCad pin end {end} is not the detected tip {exp}")
                break
            pins.append({"object": p["object"], "numbers": p["numbers"], "pos_state": p["pos_state"],
                         "kicad_iu": list(end), "page_tip": p["tip"], "verified_group": vgroup.get(p["object"])})
        if R:
            decisions.append(d)
            continue
        d.update({"decision": PLACED, "lib_symbol": cand["symbol"], "lib_id": _lib_id(base), "lib_base": base,
                  "unit": unit, "at_iu": list(at), "rot": rot, "mirror": mir,
                  "transform_choice": "ONLY" if c["transform"]["state"] == "UNIQUE" else
                  f"SYNTHETIC_CANONICAL: candidate {ci} of {len(sel)} electrically equivalent candidates",
                  "synthetic_reference": {"value": f"ECCI{c['id']}", "kind": SYNTHETIC,
                                          "note": "technical id for KiCad; the source reference is UNKNOWN"},
                  "pins": pins})
        decisions.append(d)

    withdraw_coincident(decisions)

    # nets: VERIFIED groups only
    placed_pin = {p["object"]: (d, p) for d in decisions if d["decision"] == PLACED for p in d["pins"]}
    members: dict[str, list] = {}
    for o, g in vgroup.items():
        if g is not None:
            members.setdefault(g, []).append(o)
    vedges = [e for e in conn["edges"] if e["status"] == "VERIFIED"]
    nets, partial = [], []
    for n_idx, g in enumerate(sorted(members)):
        mem = sorted(members[g])
        if set(mem) & quarantined:
            continue
        pins_in = [o for o in mem if o.startswith("p:")]
        exp = [o for o in pins_in if o in placed_pin]
        missing = sorted(set(pins_in) - set(exp))
        if missing and exp:
            partial.append({"verified_group": g, "exported_pins": exp, "pins_not_exported": missing})
        if len(exp) < 2:
            continue
        name = f"ECCI_V{len(nets) + 1}"
        es = [e for e in vedges if e["a"] in set(mem) and e["b"] in set(mem)]
        nets.append({
            "name": name, "verified_group": g, "objects": mem,
            "pins": sorted([[placed_pin[o][0]["synthetic_reference"]["value"], num, o]
                            for o in exp for num in placed_pin[o][1]["numbers"]]),
            "edges": sorted(e["id"] for e in es),
            "evidence": sorted({x for e in es for r in e["roles"] for x in r["evidence"]}),
            "prov": sorted({x for e in es for x in e["prov"]}),
            "labels": [{"object": o, "at_iu": placed_pin[o][1]["kicad_iu"],
                        "uuid": _uuid(f"{conn['content_sha256']}:label:{name}:{o}")} for o in exp],
        })
    not_exported = {
        "edges": {st: sorted(e["id"] for e in conn["edges"] if e["status"] == st)
                  for st in ("INFERRED", "UNKNOWN", "CONFLICT")},
        "site_links_inferred": sorted(x["id"] for x in conn["site_links"]),
        "open_sites": sorted(s["id"] for s in conn["sites"] if s["result"]["status"] != "INFERRED"),
        "quarantined_objects": sorted(quarantined),
        "verified_groups_partially_exported": partial,
    }
    W, H = conn["source"]["page_size"] or [0.0, 0.0]
    manifest = {
        "format": FORMAT, "schema_version": SCHEMA_VERSION,
        "inputs": {"connectivity_sha256": conn["content_sha256"], "symbols_sha256": syms["content_sha256"],
                   "library_sha256": lib_sha},
        "kicad": {"file": f"{stem}.kicad_sch", "format_version": SCH_VERSION, "sha256": None,
                  "root_uuid": _uuid(f"{conn['content_sha256']}:root")},
        "coordinate_map": {"page_pt_per_mm": k, "origin": "page top-left = schematic (0,0)", "iu_per_mm": IU_PER_MM,
                           "mirror_order": MIRROR_ORDER,
                           "paper_mm": [W / k, H / k]},
        "symbols": decisions, "nets": nets,
        "wires": [], "wires_note": "no wire geometry exported; nets are local labels on pin ends (see module doc)",
        "not_exported": not_exported,
        "claims": {"electrical_correctness": "NOT_CLAIMED",
                   "source_references": "UNKNOWN (synthetic ECCI<n> ids only)",
                   "connectivity_exported": "VERIFIED groups only"},
    }
    return manifest


# ------------------------------------------------------------------------------------------- rendering
def _mm(iu: int) -> str:
    s = f"{iu / IU_PER_MM:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def render(manifest: dict, lib_text: str) -> str:
    stem = manifest["kicad"]["file"][:-len(".kicad_sch")]
    root = manifest["kicad"]["root_uuid"]
    seed = manifest["inputs"]["connectivity_sha256"]
    W, H = manifest["coordinate_map"]["paper_mm"]
    placed = [d for d in manifest["symbols"] if d["decision"] == PLACED]
    out = [f"(kicad_sch (version {SCH_VERSION}) (generator ecci)", f"  (uuid {_quote(root)})",
           f"  (paper \"User\" {max(W, 10.0):.4f} {max(H, 10.0):.4f})", "  (lib_symbols"]
    for base in sorted({d["lib_base"] for d in placed}):
        lib_id = _lib_id(base)
        out.append("    " + library_symbol_text(lib_text, base, lib_id))
    out.append("  )")
    font = "(effects (font (size 1.27 1.27)) hide)"
    for net in manifest["nets"]:
        for lb in net["labels"]:
            x, y = lb["at_iu"]
            out.append(f"  (label {_quote(net['name'])} (at {_mm(x)} {_mm(y)} 0) "
                       f"(effects (font (size 1.27 1.27)) (justify left bottom)) (uuid {_quote(lb['uuid'])}))")
    for d in sorted(placed, key=lambda d: d["cluster"]):
        x, y = d["at_iu"]
        ref = d["synthetic_reference"]["value"]
        su = _uuid(f"{seed}:symbol:{d['cluster']}")
        mir = f" (mirror {d['mirror']})" if d["mirror"] else ""
        out.append(f"  (symbol (lib_id {_quote(d['lib_id'])}) (at {_mm(x)} {_mm(y)} {d['rot']}){mir} (unit {d['unit']})")
        out.append("    (in_bom yes) (on_board yes) (dnp no)")
        out.append(f"    (uuid {_quote(su)})")
        for prop, val in (("Reference", ref), ("Value", "UNKNOWN"), ("Footprint", ""), ("Datasheet", "")):
            out.append(f"    (property {_quote(prop)} {_quote(val)} (at {_mm(x)} {_mm(y)} 0) {font})")
        nums = sorted({n for p in d["pins"] for n in p["numbers"]})
        cid = d["cluster"]
        for n in nums:
            out.append(f"    (pin {_quote(n)} (uuid {_quote(_uuid(f'{seed}:pin:{cid}:{n}'))}))")
        out.append(f"    (instances (project {_quote(stem)} (path {_quote('/' + root)} (reference {_quote(ref)}) "
                   f"(unit {d['unit']}))))")
        out.append("  )")
    out.append("  (sheet_instances (path \"/\" (page \"1\")))")
    out.append(")")
    return "\n".join(out) + "\n"


def canonical(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n"


def export(conn: dict, syms: dict, lib_text: str, stem: str = "ecci_export") -> tuple[str, dict]:
    """Returns (kicad_sch text, manifest). The manifest records the schematic's sha256 and its own hash."""
    man = plan(conn, syms, lib_text, stem)
    sch = render(man, lib_text)
    man["kicad"]["sha256"] = hashlib.sha256(sch.encode("utf-8")).hexdigest()
    man["content_sha256"] = hashlib.sha256(canonical(man).encode()).hexdigest()
    return sch, man


def manifest_hash_ok(man: dict) -> bool:
    body = {k: v for k, v in man.items() if k != "content_sha256"}
    return man.get("content_sha256") == hashlib.sha256(canonical(body).encode()).hexdigest()


def verify_manifest(man: dict, conn: dict, syms: dict, lib_text: str, sch_text: str | None = None) -> list[str]:
    """A manifest is trusted only if re-planning from the (reader-checked) inputs gives exactly it."""
    P = []
    if not manifest_hash_ok(man):
        P.append("manifest content_sha256 mismatch")
    try:
        sch2, man2 = export(conn, syms, lib_text, man["kicad"]["file"][:-len(".kicad_sch")])
    except (ExportError, KeyError, TypeError, ValueError) as e:
        return P + [f"inputs cannot be exported: {e}"]
    if canonical(man2) != canonical(man):
        diff = sorted(k for k in set(man) | set(man2) if man.get(k) != man2.get(k))
        P.append(f"manifest is not what the inputs give (differs in {diff})")
    if sch_text is not None and sch_text != sch2:
        P.append("schematic text is not what the inputs give")
    return P
