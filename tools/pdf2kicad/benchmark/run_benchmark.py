#!/usr/bin/env python3
"""Build an auditable PDF-vs-KiCad-netlist benchmark from pinned public projects.

The parser receives only the exported PDF. The .kicad_sch is used only by
kicad-cli to produce the independent reference netlist and PDF.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import pymupdf

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf2kicad.cli import extract, Unsupported
from pdf2kicad import netlist as NL

PIN_RE = re.compile(r"^[A-Za-z0-9_+\-]+$")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=True)


def git_checkout(repo: str, commit: str, dest: Path) -> None:
    if not dest.exists():
        dest.mkdir(parents=True)
        run(["git", "init"], cwd=dest)
        run(["git", "remote", "add", "origin", f"https://github.com/{repo}.git"], cwd=dest)
    run(["git", "fetch", "--depth", "1", "origin", commit], cwd=dest)
    run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=dest)
    actual = run(["git", "rev-parse", "HEAD"], cwd=dest).stdout.strip()
    if actual != commit:
        raise RuntimeError(f"commit mismatch for {repo}: expected {commit}, got {actual}")


def parse_reference_xml(path: Path) -> tuple[set[tuple[str, str]], set[tuple[tuple[str, str], tuple[str, str]]], dict]:
    root = ET.parse(path).getroot()
    nets: dict[str, set[tuple[str, str]]] = {}
    for net in root.findall(".//nets/net"):
        name = net.attrib.get("name", "")
        nodes = set()
        for node in net.findall("node"):
            ref, pin = node.attrib.get("ref"), node.attrib.get("pin")
            if ref and pin is not None:
                nodes.add((ref.strip(), pin.strip()))
        if nodes:
            nets[name] = nodes
    pins = set().union(*nets.values()) if nets else set()
    pairs = set()
    for nodes in nets.values():
        ordered = sorted(nodes)
        pairs.update(tuple(pair) for pair in itertools.combinations(ordered, 2))
    return pins, pairs, {"net_count": len(nets), "connected_pin_count": len(pins),
                         "pair_count": len(pairs)}


def parse_pdf_predictions(pdf_path: Path) -> tuple[set[tuple[str, str]], set[tuple[tuple[str, str], tuple[str, str]]], dict]:
    doc = pymupdf.open(pdf_path)
    predicted_nets: dict[str, set[tuple[str, str]]] = {}
    unresolved_pin_candidates = 0
    parser_terminal_count = 0
    page_summaries = []
    for page_no in range(1, len(doc) + 1):
        comps, nets, words, topo = extract(str(pdf_path), page_no, full=True, mode="auto")
        pins, claimed = NL.pin_tokens(topo, comps, words)
        values = NL.values(topo, comps, words, claimed)
        pins, explicit, inferred, symbol = NL.infer_semantic_pin_identities(
            topo, comps, words, pins, values
        )
        page_node_count = 0
        for net_name, terminals in nets.items():
            for ref, terminal in terminals:
                parser_terminal_count += 1
                page_node_count += 1
                pin = pins.get(id(terminal))
                if not ref or pin is None or not PIN_RE.fullmatch(str(pin)):
                    unresolved_pin_candidates += 1
                    continue
                # Explicit printed pin tokens and parser-inferred semantic pin
                # identities are predictions; coordinate fallback is UNKNOWN.
                if id(terminal) not in explicit and id(terminal) not in inferred and str(pin) not in {"A", "K", "+", "-"}:
                    unresolved_pin_candidates += 1
                    continue
                predicted_nets.setdefault(str(net_name), set()).add((str(ref).strip(), str(pin).strip()))
        page_summaries.append({"page": page_no, "parser_components": len(comps),
                               "parser_net_labels": len(nets), "parser_terminal_rows": page_node_count})
    doc.close()
    pin_set = set().union(*predicted_nets.values()) if predicted_nets else set()
    pairs = set()
    for nodes in predicted_nets.values():
        pairs.update(tuple(pair) for pair in itertools.combinations(sorted(nodes), 2))
    return pin_set, pairs, {
        "pdf_page_count": len(page_summaries),
        "parser_terminal_rows": parser_terminal_count,
        "predicted_pin_count": len(pin_set),
        "unresolved_pin_identity_rows": unresolved_pin_candidates,
        "predicted_pair_count": len(pairs),
        "pages": page_summaries,
    }


def metrics(predicted: set, reference: set) -> dict:
    tp = len(predicted & reference)
    fp = len(predicted - reference)
    fn = len(reference - predicted)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision is not None and recall is not None and precision + recall else None)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--outdir", required=True, type=Path)
    ap.add_argument("--workspace", required=True, type=Path)
    ap.add_argument("--kicad-version", default="unknown")
    args = ap.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    args.outdir.mkdir(parents=True, exist_ok=True)
    args.workspace.mkdir(parents=True, exist_ok=True)

    repos = {}
    rows = []
    aggregate_pred_pairs, aggregate_ref_pairs = set(), set()
    aggregate_pred_pins, aggregate_ref_pins = set(), set()
    for item in manifest["projects"]:
        project_id = item["id"]
        record = {"id": project_id, "repository": item["repository"],
                  "commit": item["commit"], "license": item["license"],
                  "license_file": item["license_file"], "schematic": item["schematic"],
                  "status": "NOT_RUN"}
        sample_dir = args.outdir / "samples" / project_id
        sample_dir.mkdir(parents=True, exist_ok=True)
        try:
            repo_dir = repos.get(item["repository"])
            if repo_dir is None:
                repo_dir = args.workspace / item["repository"].replace("/", "__")
                git_checkout(item["repository"], item["commit"], repo_dir)
                repos[item["repository"]] = repo_dir
            actual_commit = run(["git", "rev-parse", "HEAD"], cwd=repo_dir).stdout.strip()
            if actual_commit != item["commit"]:
                raise RuntimeError("checked-out repository SHA differs from manifest")
            license_path = repo_dir / item["license_file"]
            if not license_path.is_file():
                raise RuntimeError(f"license file missing: {item['license_file']}")
            record["license_file_sha256"] = sha256(license_path)
            record["license_notice"] = license_path.read_text(encoding="utf-8", errors="replace")[:1200]

            schematic = repo_dir / item["schematic"]
            if not schematic.is_file():
                raise RuntimeError(f"schematic missing: {item['schematic']}")
            record["schematic_sha256"] = sha256(schematic)
            pdf_path = sample_dir / "source.pdf"
            net_path = sample_dir / "reference.net.xml"
            run(["kicad-cli", "sch", "export", "pdf", "--output", str(pdf_path), str(schematic)])
            run(["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml",
                 "--output", str(net_path), str(schematic)])
            if not pdf_path.is_file() or not net_path.is_file():
                raise RuntimeError("KiCad export command returned without both expected files")
            record["pdf_sha256"] = sha256(pdf_path)
            record["reference_netlist_sha256"] = sha256(net_path)
            record["pdf_page_count"] = len(pymupdf.open(pdf_path))
            ref_pins, ref_pairs, ref_summary = parse_reference_xml(net_path)
            pred_pins, pred_pairs, pred_summary = parse_pdf_predictions(pdf_path)
            pin_metrics = metrics(pred_pins, ref_pins)
            pair_metrics = metrics(pred_pairs, ref_pairs)
            record.update({
                "status": "EXPORTED_AND_COMPARED",
                "reference": ref_summary,
                "parser": pred_summary,
                "pin_identity_metrics": pin_metrics,
                "pairwise_connectivity_metrics": pair_metrics,
            })
            aggregate_ref_pins.update((project_id, *p) for p in ref_pins)
            aggregate_pred_pins.update((project_id, *p) for p in pred_pins)
            aggregate_ref_pairs.update((project_id, p) for p in ref_pairs)
            aggregate_pred_pairs.update((project_id, p) for p in pred_pairs)
        except Exception as exc:
            record["status"] = "ERROR"
            record["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(record)
        (sample_dir / "result.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    pin_metrics = metrics(aggregate_pred_pins, aggregate_ref_pins)
    pair_metrics = metrics(aggregate_pred_pairs, aggregate_ref_pairs)
    completed = sum(r["status"] == "EXPORTED_AND_COMPARED" for r in rows)
    summary = {
        "schema": "pdf2kicad-ground-truth-benchmark/v1",
        "policy": "The parser input is the KiCad-generated PDF only. The native schematic is used only by KiCad CLI to export the independent reference netlist and PDF.",
        "kicad_cli_version": args.kicad_version,
        "dataset_manifest_sha256": sha256(args.manifest),
        "requested_projects": len(rows),
        "completed_projects": completed,
        "failed_projects": len(rows) - completed,
        "dataset_status": "COMPLETE" if completed == len(rows) else "PARTIAL",
        "aggregate_pin_identity_metrics": pin_metrics,
        "aggregate_pairwise_connectivity_metrics": pair_metrics,
        "warning": "Metrics apply only to the pinned KiCad-generated PDF corpus. Pairwise scores do not prove accuracy on third-party PDFs such as QI9 Module IO.",
        "projects": rows,
    }
    (args.outdir / "benchmark_report.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in (
        "kicad_cli_version", "requested_projects", "completed_projects", "failed_projects",
        "dataset_status", "aggregate_pin_identity_metrics",
        "aggregate_pairwise_connectivity_metrics")}, indent=2))
    # Infrastructure success is distinct from dataset completeness and algorithm quality.
    return 0 if completed > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
