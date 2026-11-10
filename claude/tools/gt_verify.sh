#!/usr/bin/env bash
# Integrity AND completeness of the (possibly cached) ground truth before it is used.
#
# Integrity: provenance matches the pins in gt_expected.txt and every file listed in the manifest still hashes
# to the recorded value. Completeness: the manifest lists EXACTLY the files that are there (a truncated manifest,
# a deleted file or an extra undeclared file all fail), every exported project has exactly one PDF and one
# netlist, and the successful / failed project sets equal the pinned ones. Exits non-zero on any mismatch.
set -uo pipefail
OUT="${1:-gt_out}"
HERE="$(cd "$(dirname "$0")" && pwd)"
pin() { grep -E "^$1=" "$HERE/gt_expected.txt" | head -1 | cut -d= -f2-; }
prov() { grep -E "^$1=" "$OUT/PROVENANCE.txt" | head -1 | cut -d= -f2-; }
[ -f "$OUT/PROVENANCE.txt" ] && [ -f "$OUT/MANIFEST.sha256" ] || { echo "GT_VERIFY FAIL: no provenance/manifest"; exit 1; }
cat "$OUT/PROVENANCE.txt"
rc=0
fail() { echo "GT_VERIFY FAIL: $*"; rc=1; }

# ---- integrity of the pins
[ "$(prov KICAD_SOURCE_SHA)" = "$(pin KICAD_SRC_SHA)" ] || fail "source sha"
[ "$(prov KICAD_CLI)" = "$(pin KICAD_CLI_VERSION)" ] || fail "kicad version"
[ "$(prov EXPORT_SCRIPT_SHA256)" = "$(sha256sum "$HERE/gt_export.sh" | cut -d' ' -f1)" ] || fail "export script changed"
[ "$(prov EXPECTED_SHA256)" = "$(sha256sum "$HERE/gt_expected.txt" | cut -d' ' -f1)" ] || fail "pins changed"

# ---- every listed file still hashes to the recorded value
(cd "$OUT" && sha256sum --quiet -c MANIFEST.sha256) || fail "manifest"

# ---- the manifest lists exactly the files that exist (catches a truncated manifest and undeclared files)
listed=$(mktemp); present=$(mktemp)
sed -n 's/^[0-9a-f]\{64\}  //p' "$OUT/MANIFEST.sha256" | LC_ALL=C sort > "$listed"
(cd "$OUT" && find . -type f ! -name MANIFEST.sha256 -print) | LC_ALL=C sort > "$present"
if ! diff -q "$listed" "$present" >/dev/null; then
  fail "manifest inventory differs from the files present"
  echo "  only in manifest:"; LC_ALL=C comm -23 "$listed" "$present" | sed 's/^/    /' | head -20
  echo "  only on disk:";     LC_ALL=C comm -13 "$listed" "$present" | sed 's/^/    /' | head -20
fi
n_listed=$(wc -l < "$listed")
[ "$n_listed" = "$(wc -l < "$OUT/MANIFEST.sha256")" ] || fail "manifest has lines that are not hash+path"

# ---- one PDF and one netlist per exported project; project sets equal the pins
want_ok="$(pin EXPECT_EXPORT_OK)"
want_fail="$(echo "$(pin EXPECT_EXPORT_FAIL)" | tr ',' '\n' | LC_ALL=C sort | paste -sd, -)"
got_ok=0; got_fail_list=()
for d in "$OUT"/*/; do
  [ -d "$d" ] || continue
  name=$(basename "$d")
  npdf=$(find "$d" -maxdepth 1 -name '*.pdf' | wc -l)
  nxml=$(find "$d" -maxdepth 1 -name '*.xml' | wc -l)
  if [ "$npdf" = 1 ] && [ "$nxml" = 1 ]; then
    got_ok=$((got_ok+1))
    [ -s "$d/$name.pdf" ] || fail "$name: empty pdf"
    [ -s "$d/$name.xml" ] || fail "$name: empty netlist"
    find "$d/src" -name '*.kicad_sch' -print -quit 2>/dev/null | grep -q . || fail "$name: no source sheet copied"
  elif [ "$npdf" = 0 ] && [ "$nxml" = 0 ]; then
    got_fail_list+=("$name")
  else
    fail "$name: $npdf pdf / $nxml netlist (expected 1/1 or 0/0)"
  fi
done
got_fail="$(printf '%s\n' "${got_fail_list[@]+"${got_fail_list[@]}"}" | sed '/^$/d' | LC_ALL=C sort | paste -sd, -)"
[ "$got_ok" = "$want_ok" ] || fail "$got_ok exported projects, expected $want_ok"
[ "$got_fail" = "$want_fail" ] || fail "export-failure set '$got_fail' != pinned '$want_fail'"
rm -f "$listed" "$present"
[ $rc = 0 ] && echo "GT_VERIFY OK files=$n_listed projects=$got_ok export_failures=${got_fail:-none}"
exit $rc
