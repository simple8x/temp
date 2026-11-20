#!/usr/bin/env python3
"""Turn a JUnit XML into summary.json / summary.md with explicit PASS / FAIL / UNKNOWN.

UNKNOWN = skipped (could not be evaluated, e.g. cross-check tool missing). It is never counted
as PASS, and the script exits non-zero if anything is FAIL or UNKNOWN.
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main(junit: str, out_dir: str) -> int:
    root = ET.parse(junit).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    rows = []
    for suite in suites:
        for case in suite.iter("testcase"):
            status = "PASS"
            detail = ""
            for child in case:
                if child.tag in ("failure", "error"):
                    status, detail = "FAIL", (child.get("message") or "")[:300]
                elif child.tag == "skipped":
                    status, detail = "UNKNOWN", (child.get("message") or "")[:300]
            rows.append({"id": f"{case.get('classname')}::{case.get('name')}", "status": status,
                         "time": float(case.get("time") or 0), "detail": detail})
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("PASS", "FAIL", "UNKNOWN")}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps({"counts": counts, "total": len(rows), "cases": rows}, indent=1))
    lines = ["# Test summary", "", f"total={len(rows)} PASS={counts['PASS']} FAIL={counts['FAIL']} "
             f"UNKNOWN={counts['UNKNOWN']}", ""]
    by_module: dict[str, dict[str, int]] = {}
    for r in rows:
        mod = r["id"].split("::")[0]
        by_module.setdefault(mod, {"PASS": 0, "FAIL": 0, "UNKNOWN": 0})[r["status"]] += 1
    lines += ["| module | PASS | FAIL | UNKNOWN |", "|---|---|---|---|"]
    lines += [f"| {m} | {c['PASS']} | {c['FAIL']} | {c['UNKNOWN']} |" for m, c in sorted(by_module.items())]
    bad = [r for r in rows if r["status"] != "PASS"]
    if bad:
        lines += ["", "## Not passing", ""] + [f"- {r['status']} `{r['id']}` {r['detail']}" for r in bad]
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if not bad and rows else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
