#!/usr/bin/env python3
"""Fail-closed electrical audit for pdf2kicad page outputs.

This does not infer missing pin numbers. Coordinate identities and pin conflicts
remain explicit review items and cannot be promoted to verified electrical data.
"""
import csv
import json
import os
import re
import sys
from collections import defaultdict

def rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def main(root):
    pages = {}
    totals = defaultdict(int)
    all_conflicts = []
    for name in sorted(x for x in os.listdir(root) if re.fullmatch(r"page\d{2}", x)):
        base = os.path.join(root, name)
        nets = rows(os.path.join(base, "netlist.csv"))
        identities = rows(os.path.join(base, "pin_identity.csv"))
        pins = rows(os.path.join(base, "pins.csv"))
        conflicts = rows(os.path.join(base, "pin_conflicts.csv"))
        coordinate = [x for x in identities if x.get("identity_kind") == "coordinate_fallback"]
        blank_names = [x for x in pins if not (x.get("name") or "").strip()]
        page = {
            "netlist_rows": len(nets),
            "components": len({x.get("ref") for x in nets}),
            "nets": len({x.get("net") for x in nets}),
            "coordinate_fallback_rows": len(coordinate),
            "blank_pin_name_rows": len(blank_names),
            "cross_net_pin_conflicts": len(conflicts),
        }
        pages[name] = page
        for k, v in page.items():
            totals[k] += v
        all_conflicts.extend([{"page": name, **x} for x in conflicts])
    if not pages:
        raise SystemExit("No pageNN extraction directories found")
    status = "BLOCKED" if totals["cross_net_pin_conflicts"] else (
        "REVIEW_REQUIRED" if totals["coordinate_fallback_rows"] or totals["blank_pin_name_rows"] else "PASS_CANDIDATE")
    report = {
        "schema": "pdf2kicad.electrical-audit.v1",
        "status": status,
        "policy": "No synthetic pin identity is promoted to a verified physical pin number.",
        "pages": pages,
        "totals": dict(totals),
        "cross_net_pin_conflicts": all_conflicts,
        "release_eligible": status == "PASS_CANDIDATE",
    }
    out = os.path.join(root, "electrical-audit.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "pdf2kicad-run"))
