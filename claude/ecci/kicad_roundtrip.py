"""M4.2 KiCad round trip (V-W) and ground-truth comparison (V-G).

V-W: the `.kicad_sch` written by kicad_export is read by the REAL kicad-cli, which exports a netlist; the
netlist and the schematic text are read back and compared with the manifest:
  symbols   every PLACED symbol appears once with its synthetic reference and lib_id; nothing else appears
  pins      every exported pin (ref, number) appears in the netlist
  nets      each manifest net is exactly one KiCad net; a pin the manifest leaves unconnected is alone in its
            KiCad net; classified as OK / MISSING / SPLIT / MERGED / EXTRA
V-G: exported pins are mapped to independent ground-truth pins (KiCad's own netlist of the source design) by
page position; every pair of mapped pins is TP / FP (we join, GT does not) / FN (GT joins, we do not); GT pins
that were not exported are counted as UNKNOWN with the reason of their cluster when one is near.
Nothing here can turn a failure into a pass: a missing kicad-cli is reported as BLOCKED by the caller.
"""
from __future__ import annotations

import collections
import hashlib
import itertools
import math
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET

from .sexpr import child, children, parse

ROUNDTRIP_STATES = ("OK", "MISSING", "SPLIT", "MERGED", "EXTRA")


def kicad_cli() -> str | None:
    return shutil.which(os.environ.get("KICAD_CLI", "kicad-cli"))


def kicad_version(cli: str) -> str:
    r = subprocess.run([cli, "version"], capture_output=True, text=True, timeout=60)
    return (r.stdout or r.stderr).strip()


def export_netlist(cli: str, sch_path: str, xml_path: str, timeout: int = 300) -> subprocess.CompletedProcess:
    if os.path.exists(xml_path):
        os.remove(xml_path)          # a stale netlist must never be read as a fresh one
    return subprocess.run([cli, "sch", "export", "netlist", "--format", "kicadxml", "-o", xml_path, sch_path],
                          capture_output=True, text=True, timeout=timeout)


def read_netlist(xml_path: str) -> dict:
    """components {ref: lib_id}, nets {name: sorted [(ref, pin)]}."""
    root = ET.parse(xml_path).getroot()
    comps = {}
    for c in root.iter("comp"):
        ls = c.find("libsource")
        comps[c.get("ref")] = (f"{ls.get('lib')}:{ls.get('part')}" if ls is not None else None)
    nets = {}
    for n in root.iter("net"):
        nodes = sorted((x.get("ref"), x.get("pin")) for x in n.iter("node"))
        nets[n.get("name") or f"net{n.get('code')}"] = nodes
    return {"components": comps, "nets": nets}


def read_schematic(sch_text: str) -> dict:
    tree = parse(sch_text)
    if not tree or tree[0] != "kicad_sch":
        raise ValueError("not a kicad_sch")
    syms = []
    for s in children(tree, "symbol"):
        ref = next((str(p[2]) for p in children(s, "property") if len(p) > 2 and p[1] == "Reference"), None)
        syms.append((ref, str(child(s, "lib_id")[1])))
    labels = [(str(lb[1]), float(child(lb, "at")[1]), float(child(lb, "at")[2])) for lb in children(tree, "label")]
    return {"symbols": sorted(syms), "labels": sorted(labels), "wires": len(children(tree, "wire"))}


def compare_roundtrip(manifest: dict, sch_text: str, netlist: dict) -> dict:
    """Manifest vs what KiCad read back. `problems` empty means every check passed."""
    P: list[str] = []
    placed = [d for d in manifest["symbols"] if d["decision"] == "PLACED"]
    want_syms = sorted((d["synthetic_reference"]["value"], d["lib_id"]) for d in placed)
    sch = read_schematic(sch_text)
    if hashlib.sha256(sch_text.encode()).hexdigest() != manifest["kicad"]["sha256"]:
        P.append("schematic text is not the file the manifest describes")
    if sch["symbols"] != want_syms:
        P.append(f"schematic symbols {sch['symbols'][:4]} != manifest {want_syms[:4]}")
    want_labels = sorted((n["name"], x / manifest["coordinate_map"]["iu_per_mm"], y / manifest["coordinate_map"]["iu_per_mm"])
                         for n in manifest["nets"] for x, y in (lb["at_iu"] for lb in n["labels"]))
    if [(a, round(b, 4), round(c, 4)) for a, b, c in sch["labels"]] != [(a, round(b, 4), round(c, 4)) for a, b, c in want_labels]:
        P.append("schematic labels differ from the manifest")
    if sch["wires"] != len(manifest["wires"]):
        P.append("schematic wires differ from the manifest")
    comps = netlist["components"]
    if sorted(comps) != sorted(r for r, _ in want_syms):
        P.append(f"netlist components {sorted(comps)[:5]} != placed {sorted(r for r, _ in want_syms)[:5]}")
    for r, lib_id in want_syms:
        if r in comps and comps[r] != lib_id:
            P.append(f"netlist lib of {r} is {comps[r]}, manifest {lib_id}")
    net_of = {}
    for name, nodes in netlist["nets"].items():
        for nd in nodes:
            if nd in net_of:
                P.append(f"pin {nd} in two KiCad nets")
            net_of[nd] = name
    exported = {(d["synthetic_reference"]["value"], n) for d in placed for p in d["pins"] for n in p["numbers"]}
    missing_pins = sorted(exported - set(net_of))
    if missing_pins:
        P.append(f"exported pins absent from the netlist: {missing_pins[:5]}")
    extra_pins = sorted(set(net_of) - exported)
    if extra_pins:
        P.append(f"netlist pins the manifest does not export: {extra_pins[:5]}")
    states = collections.Counter()
    per_net = []
    in_manifest_net = {}
    for n in manifest["nets"]:
        want = {(r, num) for r, num, _ in n["pins"]}
        for x in want:
            in_manifest_net[x] = n["name"]
        got_nets = {net_of.get(x) for x in want}
        if None in got_nets:
            st = "MISSING"
        elif len(got_nets) > 1:
            st = "SPLIT"
        else:
            kn = next(iter(got_nets))
            st = "OK" if set(netlist["nets"][kn]) == want else "MERGED"
        states[st] += 1
        per_net.append({"net": n["name"], "state": st, "kicad_nets": sorted(x for x in got_nets if x)})
    for x in sorted(exported - set(in_manifest_net)):        # must be alone: nothing joined it
        if x in net_of and len(netlist["nets"][net_of[x]]) > 1:
            states["EXTRA"] += 1
            P.append(f"pin {x} is connected in KiCad but not in the manifest: {netlist['nets'][net_of[x]][:4]}")
    for st, nn in states.items():
        if st != "OK" and nn:
            P.append(f"{nn} manifest nets {st}")
    return {"problems": P, "net_states": {s: states.get(s, 0) for s in ROUNDTRIP_STATES},
            "nets": per_net, "symbols": len(want_syms), "exported_pins": len(exported),
            "kicad_nets": len(netlist["nets"])}


def compare_ground_truth(manifest: dict, netlist: dict, gt_pins: list, tol_pt: float = 1.0) -> dict:
    """gt_pins: objects with .x .y (page pt) .net .ref .number .hidden (gt_kicad.GtPin). Partition comparison
    of the KiCad-exported connectivity against the independent GT netlist, over the pins both know."""
    scoreable = [g for g in gt_pins if g.net and not g.hidden]
    grid = collections.defaultdict(list)
    for i, g in enumerate(scoreable):
        grid[(int(g.x // 4), int(g.y // 4))].append(i)
    net_of = {nd: name for name, nodes in netlist["nets"].items() for nd in nodes}
    mapped, unmatched, used, dup = [], [], set(), 0
    for d in manifest["symbols"]:
        if d["decision"] != "PLACED":
            continue
        ref = d["synthetic_reference"]["value"]
        for p in d["pins"]:
            x, y = p["page_tip"]
            cand = [i for gx in (-1, 0, 1) for gy in (-1, 0, 1) for i in grid[(int(x // 4) + gx, int(y // 4) + gy)]
                    if math.hypot(scoreable[i].x - x, scoreable[i].y - y) <= tol_pt]
            if len(cand) != 1 or cand[0] in used:
                unmatched.append(p["object"])
                dup += len(cand) > 1 or (len(cand) == 1 and cand[0] in used)
                continue
            used.add(cand[0])
            ours = {net_of.get((ref, n)) for n in p["numbers"]}
            if len(ours) != 1 or None in ours:
                unmatched.append(p["object"])
                continue
            mapped.append((p["object"], next(iter(ours)), scoreable[cand[0]].net))
    c = collections.Counter()
    fp_examples, fn_examples = [], []
    for (oa, ka, ga), (ob, kb, gb) in itertools.combinations(mapped, 2):
        ours, gt = ka == kb, ga == gb
        if ours and gt:
            c["TP"] += 1
        elif ours:
            c["FP"] += 1
            if len(fp_examples) < 10:
                fp_examples.append([oa, ob, ga, gb])
        elif gt:
            c["FN"] += 1
            if len(fn_examples) < 10:
                fn_examples.append([oa, ob, ga])
        else:
            c["TN"] += 1
    pos = c["TP"] + c["FN"]
    return {"gt_scoreable_pins": len(scoreable), "exported_pins_mapped": len(mapped),
            "exported_pins_unmatched": len(unmatched), "ambiguous_or_duplicate_matches": dup,
            "gt_pins_not_exported_UNKNOWN": len(scoreable) - len(used),
            "pairs": dict(TP=c["TP"], FP=c["FP"], FN=c["FN"], TN=c["TN"]),
            "precision": round(c["TP"] / (c["TP"] + c["FP"]), 4) if c["TP"] + c["FP"] else None,
            "recall_over_mapped_pairs": round(c["TP"] / pos, 4) if pos else None,
            "fp_examples": fp_examples, "fn_examples": fn_examples}
