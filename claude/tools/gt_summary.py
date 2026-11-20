"""Summarise a gt_out directory (format discovery + sanity). Prints one JSON line per project."""
import json, pathlib, re, sys

import pikepdf

root = pathlib.Path(sys.argv[1])
for d in sorted(p for p in root.iterdir() if p.is_dir()):
    row = {"project": d.name}
    pdfs = list(d.glob("*.pdf"))
    if pdfs:
        try:
            with pikepdf.open(pdfs[0]) as pdf:
                row["pages"] = len(pdf.pages)
                row["page_size_pt"] = [[round(float(x), 2) for x in pg.MediaBox[2:]] for pg in pdf.pages][:4]
            row["pdf_bytes"] = pdfs[0].stat().st_size
        except Exception as e:  # noqa: BLE001
            row["pdf_error"] = repr(e)
    schs = sorted((d / "src").rglob("*.kicad_sch"))
    row["sheets"] = len(schs)
    txt = "".join(p.read_text(errors="replace") for p in schs)
    row["placed_symbols"] = len(re.findall(r"\(symbol\s*\(lib_id", txt)) + len(re.findall(r"\(symbol\n\s*\(lib_id", txt))
    row["wires"] = len(re.findall(r"\(wire\b", txt))
    row["junctions"] = len(re.findall(r"\(junction\b", txt))
    row["labels"] = len(re.findall(r"\((?:global_label|label|hierarchical_label)\b", txt))
    row["no_connects"] = len(re.findall(r"\(no_connect\b", txt))
    row["buses"] = len(re.findall(r"\(bus\b", txt))
    xml = list(d.glob("*.xml"))
    if xml:
        x = xml[0].read_text(errors="replace")
        row["nets"] = len(re.findall(r"<net\b", x))
        row["nodes"] = len(re.findall(r"<node\b", x))
    print(json.dumps(row, sort_keys=True))
