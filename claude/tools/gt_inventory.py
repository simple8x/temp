"""Page-by-page disposition of a GT run, read back from its own gt_eval.log.

One line per page: EVAL (with pin recall), SKIP (with the pinned reason bucket) or ERROR. Written so that a
reviewer can see what happened to every page of the corpus without re-running anything.
usage: gt_inventory.py gt_eval.log
"""
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gt_eval import pins  # noqa: E402


def main(path: str) -> int:
    pin = pins()
    repeated = {x for x in pin.get("SKIP_REPEATED_SHEET", "").split(",") if x}
    no_export = {x for x in pin.get("EXPECT_EXPORT_FAIL", "").split(",") if x}
    rows, cov, tot = [], None, None
    for line in pathlib.Path(path).read_text(errors="replace").splitlines():
        if line.startswith("GT_EVAL "):
            rows.append(json.loads(line[8:]))
        elif line.startswith("GT_COVERAGE "):
            cov = json.loads(line[12:])
        elif line.startswith("GT_TOTAL "):
            tot = json.loads(line[9:])
    counts = collections.Counter()
    for r in sorted(rows, key=lambda r: r["project"]):
        name = r["project"]
        if "error" in r:
            counts["ERROR"] += 1
            print(f"ERROR {name:38s} {r['error'][:70]}")
        elif "skipped" in r:
            why = r["skipped"]
            bucket = ("repeated_sheet(pinned)" if name in repeated else
                      "no_export(pinned)" if name in no_export else "UNPINNED")
            counts["SKIP " + bucket] += 1
            print(f"SKIP  {name:38s} {bucket:24s} {why[:60]}")
        else:
            lp, lf = r.get("local_pins", 0), r.get("local_pins_found", 0)
            counts["EVAL"] += 1
            print(f"EVAL  {name:38s} pins={lf}/{lp:<6} prims={r.get('prims', 0):<7} {r.get('seconds', 0)}s")
    print("\nINVENTORY " + json.dumps(dict(sorted(counts.items())), sort_keys=True))
    if cov is not None:
        keys = ("evaluated", "unevaluated_pages", "page_coverage", "projects", "full_run", "problems")
        print("COVERAGE " + json.dumps({k: cov[k] for k in keys if k in cov}, sort_keys=True))
    if tot is not None:
        print(f"INVENTORY_CONSISTENT {tot.get('inventory_consistent')}")
    # This is also the gate that the evaluation actually RAN: a step whose script exits 0 without producing a
    # GT_TOTAL line (a missing entrypoint, a killed process, an empty log) must not leave the job green.
    problems = []
    if tot is None:
        problems.append("no GT_TOTAL line: the evaluation did not finish")
    elif tot.get("inventory_consistent") is not True:
        problems.append(f"inventory_consistent={tot.get('inventory_consistent')}")
    if cov is None:
        problems.append("no GT_COVERAGE line")
    if not counts.get("EVAL"):
        problems.append("no page was evaluated")
    for bad in ("SKIP UNPINNED", "ERROR"):
        if counts.get(bad):
            problems.append(f"{counts[bad]} page(s): {bad}")
    for p in problems:
        print("GT_INVENTORY_FAIL " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
