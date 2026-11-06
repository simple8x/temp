#!/usr/bin/env python3
"""Build a KiCad project: root sheet + one sub-sheet per page directory.
Usage: make_project.py OUTDIR [SRC_DIR_WITH_pageNN_SUBDIRS]
Page table is below; each dir must hold netlist.csv, parts.csv, pins.csv."""
import os, subprocess, sys, uuid
HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
SRC_OVERRIDE = sys.argv[2] if len(sys.argv) > 2 else None      # optional: folder holding pageNN/ dirs (e.g. pdf2kicad output)
if SRC_OVERRIDE:
    BASE = os.path.abspath(SRC_OVERRIDE)
NS = uuid.UUID("6f1c5e0a-3b7d-4a55-9d1e-0c2f4b8a9e11")
PAGES = [("page05", "Power", 5), ("page06", "RS232", 6), ("page07", "RS485", 7), ("page08", "Ethernet", 8),
         ("page09", "Controller", 9), ("page10", "Input", 10), ("page11", "Output", 11)]
PROJECT = "Module_IO"

def main(out):
    os.makedirs(out, exist_ok=True)
    root = str(uuid.uuid5(NS, "project-root"))
    sheets = []
    for i, (d, title, pno) in enumerate(PAGES):
        suid = str(uuid.uuid5(NS, "sheet-" + title))
        fn = "%02d_%s.kicad_sch" % (pno, title)
        cmd = [sys.executable, "-I", os.path.join(HERE, "gen_kicad_sch.py"),
               os.path.join(BASE, d, "netlist.csv"), os.path.join(out, fn),
               "--parts", os.path.join(BASE, d, "parts.csv"), "--pins", os.path.join(BASE, d, "pins.csv"),
               "--title", title, "--company", "MFE2 - Module IO QI9-2604-A01",
               "--instance-path", "/%s/%s" % (root, suid), "--page", str(pno - 3)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise SystemExit("generator failed for %s:\n%s%s" % (d, r.stdout, r.stderr))
        sheets.append((title, fn, suid, pno))
    L = ["(kicad_sch (version 20231120) (generator \"eeschema\") (generator_version \"8.0\")",
         " (uuid \"%s\")" % root, " (paper \"A4\")",
         " (title_block (title \"Module IO\") (company \"MFE2 - QI9-2604-A01\") "
         "(comment 1 \"Rebuilt from PDF - connectivity via net labels\"))", " (lib_symbols)"]
    for i, (title, fn, suid, pno) in enumerate(sheets):
        x, y = 25.4 + (i % 3) * 82.55, 38.1 + (i // 3) * 40.64
        L.append(" (sheet (at %s %s) (size 63.5 20.32) (fields_autoplaced yes)\n"
                 "  (stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0.0))\n  (uuid \"%s\")\n"
                 "  (property \"Sheetname\" \"%s\" (at %s %s 0) (effects (font (size 1.27 1.27)) (justify left bottom)))\n"
                 "  (property \"Sheetfile\" \"%s\" (at %s %s 0) (effects (font (size 1.27 1.27)) (justify left top)))\n"
                 "  (instances (project \"%s\" (path \"/%s\" (page \"%d\"))))\n )"
                 % (x, y, suid, title, x, round(y - 0.7, 4), fn, x, round(y + 20.32 + 0.7, 4), PROJECT, root, pno - 3))
    L.append(" (sheet_instances (path \"/\" (page \"1\")))")
    L.append(")")
    open(os.path.join(out, PROJECT + ".kicad_sch"), "w").write("\n".join(L) + "\n")
    open(os.path.join(out, PROJECT + ".kicad_pro"), "w").write(
        '{\n  "meta": {\n    "filename": "%s.kicad_pro",\n    "version": 1\n  }\n}\n' % PROJECT)
    print("project written to", out)

if __name__ == "__main__":
    if len(sys.argv) not in (2, 3): raise SystemExit(__doc__)
    main(sys.argv[1])
