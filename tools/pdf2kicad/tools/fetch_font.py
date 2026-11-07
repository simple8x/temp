#!/usr/bin/env python3
"""Download KiCad's stroke font (newstroke_font.cpp) and keep the ASCII glyphs as JSON.

Needs `git` and access to github.com.  Result: data/newstroke_ascii.json
"""
import json, os, re, shutil, subprocess, sys, tempfile

REPO = "https://github.com/KiCad/kicad-source-mirror.git"
PATH = "common/newstroke_font.cpp"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "newstroke_ascii.json")


def unescape(s):
    return s.replace('\\\\', '\x00').replace('\\"', '"').replace('\x00', '\\')


def parse(src):
    start = src.index("newstroke_font[] =")
    lits = re.findall(r'^\s*"((?:[^"\\]|\\.)*)",', src[start:], re.M)
    glyphs = {}
    for i in range(95):                     # U+0020 .. U+007E
        glyphs[chr(0x20 + i)] = unescape(lits[i])
    return glyphs


def main():
    tmp = tempfile.mkdtemp()
    try:
        subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--no-checkout", REPO, tmp],
                       check=True, capture_output=True, text=True, timeout=300)
        subprocess.run(["git", "-C", tmp, "checkout", "HEAD", "--", PATH], check=True,
                       capture_output=True, text=True, timeout=300)
        src = open(os.path.join(tmp, PATH), encoding="utf-8", errors="replace").read()
    except subprocess.CalledProcessError as e:
        sys.exit("git failed: %s\n%s" % (e, e.stderr))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    g = parse(src)
    assert g["1"].startswith("H\\"), "unexpected glyph encoding for '1'"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(g, open(OUT, "w"), indent=0)
    print("wrote %s (%d glyphs)" % (os.path.abspath(OUT), len(g)))


if __name__ == "__main__":
    main()
