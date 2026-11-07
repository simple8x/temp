"""CLI for independent raw-PDF graph diagnostics.
Usage: python -m pdf2kicad.reference_graph_cli source.pdf 5 6 7 --outdir out/reference-graph
"""
import argparse
import json
from pathlib import Path
from .pdf_reference_graph import build_pdf_reference_graph, compare_parser_memberships


def main(argv=None):
    ap=argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("pages",nargs="+",type=int)
    ap.add_argument("--outdir",required=True)
    ap.add_argument("--evidence-dir",help="optional directory containing page-N.connectivity_evidence.json")
    args=ap.parse_args(argv)
    out=Path(args.outdir); out.mkdir(parents=True,exist_ok=True)
    for page in args.pages:
        graph=build_pdf_reference_graph(args.pdf,page)
        target=out/f"page-{page}.pdf-reference-graph.json"
        target.write_text(json.dumps(graph,indent=2),encoding="utf-8")
        print(f"page={page} segments={len(graph['segments'])} components={len(graph['components'])} intersections={len(graph['intersections'])} scope={graph['scope']}")
        if args.evidence_dir:
            evidence_root = Path(args.evidence_dir)
            evidence_candidates = [
                evidence_root / f"page-{page}.connectivity_evidence.json",
                evidence_root / f"page-{page}" / "connectivity_evidence.json",
                evidence_root / f"page{page:02d}" / "connectivity_evidence.json",
            ]
            evidence_path = next((candidate for candidate in evidence_candidates if candidate.exists()), evidence_candidates[0])
            if evidence_path.exists():
                evidence=json.loads(evidence_path.read_text(encoding="utf-8"))
                comparison=compare_parser_memberships(graph,evidence)
                (out/f"page-{page}.graph-comparison.json").write_text(json.dumps(comparison,indent=2),encoding="utf-8")
                print(f"  geometry_candidates={comparison['geometry_candidates']} exceptions={comparison['exceptions']} electrical_verified=0")
            else:
                print(f"  comparison=SKIPPED evidence_missing={evidence_path}")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
