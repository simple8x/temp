#!/usr/bin/env python3
"""Independent electrical-topology core and adversarial oracle.

This module is deliberately not a PDF stroke classifier. It accepts explicitly
typed wire primitives and independently reviewed junction/NC marks. The type
labels and expected partitions in the fixture corpus are ground truth inputs,
not predictions. PDF-derived strokes must remain candidates until classified.
"""
from __future__ import annotations
import argparse, json, math
from pathlib import Path

EPS = 1e-8

def distance(a, b):
    return math.hypot(a[0]-b[0], a[1]-b[1])

def point_on_segment(p, a, b, tol):
    dx, dy = b[0]-a[0], b[1]-a[1]
    den = dx*dx + dy*dy
    if den <= EPS:
        return distance(p, a) <= tol
    t = ((p[0]-a[0])*dx + (p[1]-a[1])*dy) / den
    if t < -EPS or t > 1+EPS:
        return False
    q = (a[0]+t*dx, a[1]+t*dy)
    return distance(p, q) <= tol

def proper_crossing(a, b, c, d):
    den = (b[0]-a[0])*(d[1]-c[1])-(b[1]-a[1])*(d[0]-c[0])
    if abs(den) <= EPS:
        return None
    t = ((c[0]-a[0])*(d[1]-c[1])-(c[1]-a[1])*(d[0]-c[0]))/den
    u = ((c[0]-a[0])*(b[1]-a[1])-(c[1]-a[1])*(b[0]-a[0]))/den
    if EPS < t < 1-EPS and EPS < u < 1-EPS:
        return [a[0]+t*(b[0]-a[0]), a[1]+t*(b[1]-a[1])]
    return None

class DSU:
    def __init__(self, n): self.p=list(range(n))
    def find(self, x):
        if self.p[x] != x: self.p[x]=self.find(self.p[x])
        return self.p[x]
    def union(self, a,b):
        a,b=self.find(a),self.find(b)
        if a != b: self.p[b]=a

def solve(case, tolerance=1e-6):
    """Return candidate electrical partitions only from typed wire evidence."""
    segs=[s for s in case["segments"] if s["role"] == "wire"]
    dsu=DSU(len(segs))
    dots=case.get("junctions", [])
    ncs=case.get("no_connects", [])
    evidence=[]
    for i, left in enumerate(segs):
        for j in range(i+1, len(segs)):
            right=segs[j]
            a,b=left["a"],left["b"]; c,d=right["a"],right["b"]
            contacts = any(point_on_segment(p,c,d,tolerance) for p in (a,b)) or any(
                point_on_segment(p,a,b,tolerance) for p in (c,d))
            crossing=proper_crossing(a,b,c,d)
            point=crossing
            if contacts:
                point=next((p for p in (a,b,c,d) if point_on_segment(
                    p, c,d,tolerance) and point_on_segment(p,a,b,tolerance)), None)
            if point is None and not contacts:
                continue
            blocked=any(distance(point, nc) <= tolerance for nc in ncs) if point else False
            marked=any(distance(point, dot) <= tolerance for dot in dots) if point else False
            if blocked:
                relation="NO_CONNECT_BLOCKED"
            elif contacts:
                dsu.union(i,j); relation="ENDPOINT_OR_T_CONTACT_JOINED"
            elif crossing and marked:
                dsu.union(i,j); relation="CROSSING_WITH_EXPLICIT_JUNCTION_JOINED"
            elif crossing:
                relation="CROSSING_WITHOUT_JUNCTION_SEPARATE"
            else:
                relation="UNKNOWN_CONTACT"
            evidence.append({"segments":[left["id"],right["id"]],"point":point,"relation":relation})
    groups={}
    for i,s in enumerate(segs):
        groups.setdefault(dsu.find(i),[]).append(s["id"])
    partitions=sorted([sorted(v) for v in groups.values()])
    return {"partitions":partitions,"evidence":evidence,
            "authority":"ORACLE_FIXTURE_ONLY" if case.get("oracle_case") else "CANDIDATE_ONLY"}

def canonical(parts):
    return sorted([sorted(x) for x in parts])

def run_corpus(path):
    corpus=json.loads(Path(path).read_text(encoding="utf-8"))
    rows=[]
    for case in corpus["cases"]:
        actual=solve(case)["partitions"]
        expected=canonical(case["expected_partitions"])
        rows.append({"case_id":case["id"],"expected":expected,"actual":actual,
                     "pass":actual == expected})
    passed=sum(x["pass"] for x in rows)
    return {"schema":"qi9-independent-electrical-oracle/v1",
            "corpus_id":corpus["corpus_id"],"case_count":len(rows),
            "passed":passed,"failed":len(rows)-passed,"cases":rows,
            "status":"PASS" if passed == len(rows) else "FAIL",
            "scope":"synthetic labeled topology only; does not establish PDF stroke roles or QI9 netlist truth"}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--corpus",default=str(Path(__file__).with_name("fixtures")/"electrical_topology_oracle.json"))
    ap.add_argument("--out")
    args=ap.parse_args()
    result=run_corpus(args.corpus)
    payload=json.dumps(result,indent=2)
    print(payload)
    if args.out: Path(args.out).write_text(payload+"\n",encoding="utf-8")
    if result["failed"]: raise SystemExit(1)

if __name__ == "__main__": main()
