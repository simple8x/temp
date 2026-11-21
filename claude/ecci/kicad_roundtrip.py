"""M4.2 KiCad round trip (V-W) and ground-truth comparison (V-G).

V-W: the `.kicad_sch` written by kicad_export is read by the REAL kicad-cli, which exports a netlist; the
netlist and the schematic text are read back and compared with the manifest:
  symbols   every PLACED symbol appears once with its synthetic reference and lib_id; nothing else appears
  pins      every exported pin (ref, number) appears in the netlist
  nets      each manifest net is exactly one KiCad net; a pin the manifest leaves unconnected is alone in its
            KiCad net; classified as OK / MISSING / SPLIT / MERGED / EXTRA
V-G: exported pins are mapped to independent ground-truth pins (KiCad's own netlist of the source design) by
page position, then CONFIRMED pin numbers are checked against GT pin numbers. Every pair of mapped pins is TP /
FP (we join, GT does not) / FN (GT joins, we do not); any FN blocks PASS. GT pins that were not exported or
whose identity/net cannot be scored are counted as UNKNOWN with a reason.
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
    # claimed: GT indices an exported pin matched geometrically (duplicate-match protection: a GT pin is claimed at
    # most once, by the first exported pin in manifest order). scored: claimed GT indices whose exported pin is on
    # exactly one KiCad net, i.e. that enter the pair scoring. Every scoreable GT pin not in `scored` is UNKNOWN.
    mapped, unmatched, claimed, scored, dup = [], [], {}, set(), 0
    identity_mismatches, identity_unverified = [], []
    unscored_reasons = {}
    for d in manifest["symbols"]:
        if d["decision"] != "PLACED":
            continue
        ref = d["synthetic_reference"]["value"]
        for p in d["pins"]:
            x, y = p["page_tip"]
            cand = [i for gx in (-1, 0, 1) for gy in (-1, 0, 1) for i in grid[(int(x // 4) + gx, int(y // 4) + gy)]
                    if math.hypot(scoreable[i].x - x, scoreable[i].y - y) <= tol_pt]
            if len(cand) != 1 or cand[0] in claimed:
                why = ("no GT pin within tolerance" if not cand else
                       "several GT pins within tolerance" if len(cand) > 1 else "GT pin already matched")
                unmatched.append((p["object"], why))
                dup += len(cand) > 1 or (len(cand) == 1 and cand[0] in claimed)
                continue
            claimed[cand[0]] = p["object"]
            gt_pin = scoreable[cand[0]]
            assignment = p.get("number_assignment")
            if assignment == "EQUIVALENT_CANONICAL":
                reason = f"matched by {p['object']}; synthetic canonical numbering means source pin identity is not proven and is excluded from GT PASS"
                identity_unverified.append({"object": p["object"], "gt_ref": gt_pin.ref,
                                            "gt_number": gt_pin.number,
                                            "export_numbers": list(p["numbers"]), "reason": reason})
                unmatched.append((p["object"], reason))
                unscored_reasons[cand[0]] = reason
                continue
            elif assignment not in ("CONFIRMED", "TRANSFORM_EQUIVALENT"):
                reason = f"matched by {p['object']} but pin identity assignment is missing or unknown"
                unmatched.append((p["object"], reason))
                unscored_reasons[cand[0]] = reason
                continue
            elif list(map(str, p["numbers"])) != [str(gt_pin.number)]:
                identity_mismatches.append({"object": p["object"], "gt_ref": gt_pin.ref,
                                            "gt_number": str(gt_pin.number),
                                            "export_numbers": list(map(str, p["numbers"]))})
                reason = "pin identity mismatch against GT number"
                unmatched.append((p["object"], reason))
                unscored_reasons[cand[0]] = reason
                continue
            ours = {net_of.get((ref, n)) for n in p["numbers"]}
            if len(ours) != 1 or None in ours:
                reason = f"matched by {p['object']} but that pin is not on exactly one KiCad net"
                unmatched.append((p["object"], reason))
                unscored_reasons[cand[0]] = reason
                continue
            scored.add(cand[0])                  # only now: the GT pin is electrically scored
            mapped.append((p["object"], next(iter(ours)), gt_pin.net))
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
            # every scoreable GT pin that was NOT electrically scored (not claimed by any exported pin, or claimed by
            # one that is not on exactly one KiCad net); semantics since the B3 fix: "not scored", not "not claimed"
            "gt_pins_not_exported_UNKNOWN": len(scoreable) - len(scored),
            "gt_pins_scored": len(scored),
            "gt_pins_claimed_not_scored_UNKNOWN": len(set(claimed) - scored),
            "pin_identity_mismatches": identity_mismatches,
            "pin_identity_unverified_synthetic_canonical": identity_unverified,
            # mapping coverage = GT scoreable pins that an exported pin was scored against (None: nothing to score)
            "mapping_coverage": round(len(mapped) / len(scoreable), 4) if scoreable else None,
            # unmatched pins are not scored and stay UNKNOWN: nothing is inferred about their connectivity
            "unmatched_exported_pins": [{"object": o, "state": "UNKNOWN", "reason": r} for o, r in sorted(unmatched)],
            "unmatched_gt_pins": sorted([{"ref": g.ref, "number": g.number, "state": "UNKNOWN",
                                          "reason": "no exported pin matched" if i not in claimed else
                                          unscored_reasons.get(i, f"matched by {claimed[i]} but that pin is not on exactly one KiCad net")}
                                         for i, g in enumerate(scoreable) if i not in scored],
                                        key=lambda d: (str(d["ref"]), str(d["number"]))),
            "pairs": dict(TP=c["TP"], FP=c["FP"], FN=c["FN"], TN=c["TN"]),
            "precision": round(c["TP"] / (c["TP"] + c["FP"]), 4) if c["TP"] + c["FP"] else None,
            "recall_over_mapped_pairs": round(c["TP"] / pos, 4) if pos else None,
            "fp_examples": fp_examples, "fn_examples": fn_examples}


# ---- ground-truth gate -------------------------------------------------------------------------------------------
GT_PASS, GT_FAIL, GT_BLOCKED, GT_INCONCLUSIVE = "PASS", "FAIL", "BLOCKED", "INCONCLUSIVE"
GT_POLICY_KEYS = frozenset({"min_mapping_coverage"})


def gt_policy_problems(policy) -> list[str]:
    """A GT policy is {"min_mapping_coverage": x} with 0 < x <= 1. The project documents no M4.2 coverage threshold,
    so none is assumed here: the caller must supply one (see docs/M4_2_KICAD_EXPORT.md)."""
    if policy is None:
        return ["no ground-truth policy (min_mapping_coverage) configured"]
    if not isinstance(policy, dict) or set(policy) != GT_POLICY_KEYS:
        return [f"ground-truth policy must have exactly the keys {sorted(GT_POLICY_KEYS)}"]
    x = policy["min_mapping_coverage"]
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not 0 < x <= 1:
        return [f"min_mapping_coverage {x!r} is not a number in (0, 1]"]
    return []


def load_gt_policy(path) -> dict | None:
    """The policy file (JSON), or None if the path is unset / missing. A malformed file raises ValueError."""
    import json
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        pol = json.load(f, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(f"constant {c}")))
    probs = gt_policy_problems(pol)
    if probs:
        raise ValueError(f"{path}: {probs[0]}")
    return pol


def ground_truth_gate(g: dict, policy) -> dict:
    """PASS needs ALL of: a valid policy, at least one scored pin pair (>= 2 mapped pins), mapping coverage >= the
    policy minimum, and FP == 0. FP > 0 is FAIL whatever else holds (a wrong connection was exported); no policy
    is BLOCKED; nothing scoreable is INCONCLUSIVE; coverage below the minimum is FAIL. Unmatched pins stay UNKNOWN."""
    pairs = g["pairs"]
    scored = sum(pairs.values())
    rep = {"gt_scoreable_pins": g["gt_scoreable_pins"], "exported_pins_mapped": g["exported_pins_mapped"],
           "exported_pins_unmatched": g["exported_pins_unmatched"], "mapping_coverage": g["mapping_coverage"],
           "TP": pairs["TP"], "FP": pairs["FP"], "FN": pairs["FN"], "TN": pairs["TN"],
           "precision": g["precision"], "recall": g["recall_over_mapped_pairs"],
           "pin_identity_unverified_synthetic_canonical": g.get("pin_identity_unverified_synthetic_canonical", []),
           "min_mapping_coverage": policy.get("min_mapping_coverage") if isinstance(policy, dict) else None}
    if pairs["FP"] > 0 or pairs["FN"] > 0:
        failures = []
        if pairs["FP"] > 0:
            failures.append(f"{pairs['FP']} false-positive pin pair(s)")
        if pairs["FN"] > 0:
            failures.append(f"{pairs['FN']} false-negative pin pair(s)")
        return {**rep, "state": GT_FAIL, "reason": "; ".join(failures)}
    if g.get("pin_identity_mismatches"):
        return {**rep, "state": GT_FAIL,
                "reason": f"{len(g['pin_identity_mismatches'])} exported pin identity mismatch(es) against GT"}
    pp = gt_policy_problems(policy)
    if pp:
        return {**rep, "state": GT_BLOCKED, "reason": pp[0]}
    if g["exported_pins_mapped"] < 2 or scored == 0 or g["mapping_coverage"] is None:
        return {**rep, "state": GT_INCONCLUSIVE,
                "reason": f"{g['exported_pins_mapped']} exported pin(s) mapped to GT: no pin pair could be scored"}
    if g["mapping_coverage"] < policy["min_mapping_coverage"]:
        return {**rep, "state": GT_FAIL, "reason": f"mapping coverage {g['mapping_coverage']} below the policy "
                                                   f"minimum {policy['min_mapping_coverage']}"}
    if g.get("pin_identity_unverified_synthetic_canonical"):
        return {**rep, "state": GT_INCONCLUSIVE,
                "reason": f"{len(g['pin_identity_unverified_synthetic_canonical'])} synthetic-canonical pin(s) have no source pin identity; excluded from GT PASS"}
    if g["precision"] is None and pairs["TP"] == 0:
        return {**rep, "state": GT_PASS, "reason": "FP = 0, FN = 0, coverage met; no exported connection among mapped pins"}
    return {**rep, "state": GT_PASS, "reason": "FP = 0, FN = 0 and coverage met"}
