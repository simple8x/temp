"""M4.2 one-page pipeline used by the tools: PDF page -> documents (through their readers) -> KiCad -> checks.

Every stage writes its artifact before the next one runs, so a failure leaves the evidence of how far it got.
States are explicit: a stage that could not run is BLOCKED (with the reason), never PASS.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import kicad_export as kx
from . import kicad_roundtrip as kr
from . import symrec
from .writer import dumps as conn_dumps, loads as conn_loads

PASS, FAIL, BLOCKED = "PASS", "FAIL", "BLOCKED"
INCONCLUSIVE = "INCONCLUSIVE"


def run_page(out: Path, pi, cls, clusters, built, res, library, lib_text: str, source: dict,
             gt_pins=None, cli: str | None = None, gt_policy: dict | None = None) -> dict:
    """`gt_policy` ({"min_mapping_coverage": x}) is required for the ground_truth stage to PASS: without it the stage
    is BLOCKED (the project documents no M4.2 coverage threshold, so none is assumed)."""
    out.mkdir(parents=True, exist_ok=True)
    rec: dict = {"source": source, "stages": {}}
    S = rec["stages"]
    # 1. connectivity document through its fail-closed reader
    t = conn_dumps(built.graph, res, built=built, page=pi, clusters=clusters, registry=cls.registry, source=source)
    (out / "connectivity.json").write_text(t)
    conn = conn_loads(t)
    S["connectivity_schema_roundtrip"] = PASS if conn_dumps(built.graph, res, built=built, page=pi, clusters=clusters,
                                                             registry=cls.registry, source=source) == t else FAIL
    # 2. symbol document through its reader (re-matches the whole library) + the page audit
    st = symrec.dumps(symrec.build_symbols(clusters, pi, library, conn, check=False))
    (out / "symbols.json").write_text(st)
    syms = symrec.loads(st, conn, library)
    S["symbols_schema_roundtrip"] = PASS if symrec.dumps(syms) == st else FAIL
    audit = symrec.audit_against_page(syms, pi) + symrec.audit_evidence(conn, syms, pi, library)
    pla = symrec.audit_pin_lines(syms, pi, cls)
    rec["pin_lines_audit"] = {"status": pla["status"], "clusters": pla["clusters"], "problems": pla["problems"][:20]}
    ur = conn["unresolved_references"]
    other = audit + [f"unresolved {k}: {v[:3]}" for k, v in ur.items()
                     if v and k not in ("objects_without_primitives", "sites_without_arm_geometry")]
    prov_problems = other + pla["problems"]
    S["provenance"] = (FAIL if other or pla["status"] == symrec.PL_FAIL
                       else BLOCKED if pla["status"] == symrec.PL_BLOCKED else PASS)
    rec["provenance_problems"] = prov_problems[:20]
    # 3. export + manifest re-derivation
    sch, man = kx.export(conn, syms, lib_text)
    (out / "ecci_export.kicad_sch").write_text(sch)
    (out / "manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True))
    vm = kx.verify_manifest(man, conn, syms, lib_text, sch)
    S["manifest_rederived"] = PASS if not vm else FAIL
    rec["manifest_problems"] = vm
    placed = [d for d in man["symbols"] if d["decision"] == "PLACED"]
    rec["symbols"] = {"clusters": len(man["symbols"]), "placed": len(placed),
                      "not_placed_reasons": _reasons(man)}
    rec["nets_exported"] = len(man["nets"])
    rec["not_exported"] = {k: (len(v) if isinstance(v, list) else {a: len(b) for a, b in v.items()})
                           for k, v in man["not_exported"].items()}
    rec["unknown_fields"] = {"source_reference_UNKNOWN": sum(c["reference"]["state"] == "UNKNOWN" for c in syms["clusters"]),
                             "source_value_UNKNOWN": sum(c["value"]["state"] == "UNKNOWN" for c in syms["clusters"]),
                             "identity_UNKNOWN": sum(c["identity"]["state"] == "UNKNOWN" for c in syms["clusters"]),
                             "identity_AMBIGUOUS": sum(c["identity"]["state"] == "AMBIGUOUS" for c in syms["clusters"]),
                             "transform_AMBIGUOUS": sum(c["transform"]["state"] == "AMBIGUOUS" for c in syms["clusters"]),
                             "pin_id_AMBIGUOUS_or_UNKNOWN": sum(p["id_state"] not in ("CONFIRMED", "EQUIVALENT")
                                                                for c in syms["clusters"] for p in c["pins"]),
                             "pin_id_ELECTRICALLY_EQUIVALENT": sum(p["id_state"] == "EQUIVALENT"
                                                                    for c in syms["clusters"] for p in c["pins"])}
    # 4. the real kicad-cli
    if cli is None:
        S["kicad_read"] = S["netlist_roundtrip"] = S["ground_truth"] = BLOCKED
        rec["blocked"] = "kicad-cli not found"
        return rec
    xml = out / "netlist.xml"
    t0 = time.time()
    r = kr.export_netlist(cli, str(out / "ecci_export.kicad_sch"), str(xml))
    (out / "kicad_cli.log").write_text(f"exit {r.returncode}\n{r.stdout}\n{r.stderr}")
    fresh = xml.exists() and xml.stat().st_mtime >= t0 - 1
    if r.returncode != 0 or not fresh:
        S["kicad_read"] = FAIL
        S["netlist_roundtrip"] = S["ground_truth"] = BLOCKED
        rec["kicad_error"] = (r.stderr or r.stdout)[-400:]
        return rec
    S["kicad_read"] = PASS
    nl = kr.read_netlist(str(xml))
    rt = kr.compare_roundtrip(man, sch, nl)
    (out / "roundtrip.json").write_text(json.dumps(rt, indent=1, sort_keys=True))
    S["netlist_roundtrip"] = PASS if not rt["problems"] else FAIL
    rec["roundtrip"] = {k: rt[k] for k in ("net_states", "symbols", "exported_pins", "kicad_nets")}
    rec["roundtrip_problems"] = rt["problems"][:10]
    if gt_pins is None:
        S["ground_truth"] = BLOCKED
        return rec
    g = kr.compare_ground_truth(man, nl, gt_pins)
    gate = kr.ground_truth_gate(g, gt_policy)
    g["gate"] = gate
    (out / "gt_report.json").write_text(json.dumps(g, indent=1, sort_keys=True))
    S["ground_truth"] = gate["state"]          # PASS / FAIL / BLOCKED / INCONCLUSIVE (never PASS by default)
    rec["ground_truth"] = {k: g[k] for k in ("pairs", "precision", "recall_over_mapped_pairs", "gt_scoreable_pins",
                                             "exported_pins_mapped", "exported_pins_unmatched",
                                             "gt_pins_not_exported_UNKNOWN", "mapping_coverage")}
    rec["ground_truth"]["gate"] = {k: gate[k] for k in ("state", "reason", "min_mapping_coverage")}
    return rec


def _reasons(man) -> dict:
    out: dict[str, int] = {}
    for d in man["symbols"]:
        for r in d["reasons"]:
            key = r.split(" (")[0].split(":")[0]
            out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def gate_table(records: list[dict], cli: str | None, extra: dict | None = None) -> dict:
    """Aggregate per-stage states: PASS only if EVERY record passed that stage; FAIL if any failed; otherwise
    (BLOCKED, INCONCLUSIVE or any unknown state) BLOCKED. Only the literal PASS counts as a pass."""
    gates = {}
    for st in ("connectivity_schema_roundtrip", "symbols_schema_roundtrip", "provenance", "manifest_rederived",
               "kicad_read", "netlist_roundtrip", "ground_truth"):
        vals = [r["stages"].get(st, BLOCKED) for r in records] or [BLOCKED]
        gates[st] = FAIL if FAIL in vals else (PASS if all(v == PASS for v in vals) else BLOCKED)
    if extra:
        gates.update(extra)
    return {"kicad_cli": cli, "gates": gates, "overall": PASS if all(v == PASS for v in gates.values()) else
            (FAIL if FAIL in gates.values() else "BLOCKED_OR_PARTIAL")}


def env_cli() -> str | None:
    return kr.kicad_cli() if os.environ.get("ECCI_NO_KICAD") != "1" else None