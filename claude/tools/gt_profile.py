"""Profile one GT page.  usage: gt_profile.py gt_out project page   (page 0-based; project is the folder name)"""
import cProfile
import io
import pathlib
import pstats
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gt_eval  # noqa: E402

root, proj, page = pathlib.Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
pdir = root / proj
schs = sorted((pdir / "src").rglob("*.kicad_sch"))
t0 = time.time()
mp = gt_eval.map_pages_to_sheets(str(sorted(pdir.glob("*.pdf"))[0]), schs, __import__("pikepdf").open(sorted(pdir.glob("*.pdf"))[0]).pages.__len__())
print("PROFILE map_pages seconds", round(time.time() - t0, 1), mp.get(page))
si = mp[page][0]
pr = cProfile.Profile()
pr.enable()
t1 = time.time()
r = gt_eval.evaluate(pdir, page, schs[si], f"{proj}#p{page}")
pr.disable()
print("PROFILE evaluate seconds", round(time.time() - t1, 1), "prims", r.get("prims"), "gt_pins", r.get("gt_pins"))
buf = io.StringIO()
pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(28)
for line in buf.getvalue().splitlines():
    print("PROFILE", line[:170])
