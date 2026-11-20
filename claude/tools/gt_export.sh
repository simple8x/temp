#!/usr/bin/env bash
# Ground-truth export (M4.0). Runs on the CI runner: KiCad itself renders the PDF and computes the netlist,
# so the reference connectivity is independent of the ECCI algorithm. Nothing here is committed to the repo.
# Fail-closed: the KiCad source commit and CLI version are pinned in gt_source.txt and checked; any export
# outcome other than the pinned one exits non-zero. Provenance and a SHA-256 manifest are written into OUT.
set -uo pipefail
OUT="${1:-gt_out}"
HERE="$(cd "$(dirname "$0")" && pwd)"
pin() { grep -hE "^$1=" "$HERE/gt_source.txt" "$HERE/gt_pages.txt" | head -1 | cut -d= -f2-; }
SRC_SHA="$(pin KICAD_SRC_SHA)"; CLI_WANT="$(pin KICAD_CLI_VERSION)"
EXP_OK="$(pin EXPECT_EXPORT_OK)"; EXP_FAIL="$(pin EXPECT_EXPORT_FAIL)"
[ -n "$SRC_SHA" ] && [ -n "$CLI_WANT" ] && [ -n "$EXP_OK" ] || { echo "PIN MISSING in gt_source.txt"; exit 2; }
CLI_HAVE="$(kicad-cli version 2>&1 | head -1 | tr -d '\r')"
mkdir -p "$OUT"
if [ "${GT_SELFTEST:-}" = 1 ]; then
  # Self-test of this script's own failure handling (see tests/unit/test_gt_failclosed.py): the demos come from
  # GT_SRC_DIR instead of the pinned upstream commit, and the provenance says SELFTEST, so the result can never
  # pass gt_verify.sh as ground truth. Nothing else in the script behaves differently.
  [ -d "${GT_SRC_DIR:-}" ] || { echo "GT_SRC_DIR missing"; exit 2; }
  rm -rf ks && mkdir -p ks && cp -r "$GT_SRC_DIR" ks/demos
  HAVE_SHA=SELFTEST
else
  [ "$CLI_HAVE" = "$CLI_WANT" ] || { echo "KICAD_CLI_MISMATCH have='$CLI_HAVE' want='$CLI_WANT'"; exit 2; }
  rm -rf ks && mkdir ks
  git -C ks init -q && git -C ks remote add origin https://github.com/KiCad/kicad-source-mirror.git || { echo "INIT FAILED"; exit 2; }
  git -C ks sparse-checkout set demos || { echo "SPARSE FAILED"; exit 2; }
  git -C ks fetch -q --depth 1 --filter=blob:none origin "$SRC_SHA" && git -C ks checkout -q FETCH_HEAD || { echo "FETCH FAILED $SRC_SHA"; exit 2; }
  HAVE_SHA="$(git -C ks rev-parse HEAD)"
  [ "$HAVE_SHA" = "$SRC_SHA" ] || { echo "SOURCE_SHA_MISMATCH have=$HAVE_SHA want=$SRC_SHA"; exit 2; }
fi
echo "KICAD_SOURCE_SHA=$HAVE_SHA"
echo "KICAD_CLI=$CLI_HAVE"
n_ok=0; fails=()
while IFS= read -r pro; do
  d=$(dirname "$pro"); name=$(basename "$pro" .kicad_pro); sch="$d/$name.kicad_sch"
  if [ ! -f "$sch" ]; then echo "SKIP(no root sheet) $pro"; continue; fi
  tgt="$OUT/$name"; mkdir -p "$tgt/src"
  tgt_abs="$(cd "$tgt" && pwd)"          # cp --parents needs an existing absolute destination; OUT may be relative
  # copying the source sheets must not fail silently: the sheets are what gt_eval maps pages against
  nsrc=$(cd "$d" && find . -name '*.kicad_sch' | wc -l)
  if ! (cd "$d" && find . -name '*.kicad_sch' -print0 | xargs -0 -I{} cp --parents {} "$tgt_abs/src/"); then
    echo "FAIL copy-sheets $name"; fails+=("$name"); rm -rf "$tgt"; continue
  fi
  ncopied=$(find "$tgt/src" -name '*.kicad_sch' | wc -l)
  if [ "$nsrc" != "$ncopied" ]; then
    echo "FAIL copy-sheets $name ($ncopied of $nsrc)"; fails+=("$name"); rm -rf "$tgt"; continue
  fi
  ok=1
  kicad-cli sch export pdf --output "$tgt/$name.pdf" "$sch" >"$tgt/pdf.log" 2>&1 || { echo "FAIL pdf $name"; ok=0; }
  kicad-cli sch export netlist --format kicadxml --output "$tgt/$name.xml" "$sch" >"$tgt/net.log" 2>&1 || { echo "FAIL netlist $name"; ok=0; }
  if [ $ok = 1 ]; then n_ok=$((n_ok+1)); else fails+=("$name"); rm -f "$tgt/$name.pdf" "$tgt/$name.xml"; fi
done < <(find ks/demos -name '*.kicad_pro' | sort)
got_fail="$(printf '%s\n' "${fails[@]}" | sort | paste -sd, -)"
want_fail="$(echo "$EXP_FAIL" | tr ',' '\n' | sort | paste -sd, -)"
echo "EXPORT_OK=$n_ok EXPORT_FAIL=${#fails[@]} ($got_fail)"
{
  echo "KICAD_SOURCE_SHA=$HAVE_SHA"
  echo "KICAD_CLI=$CLI_HAVE"
  echo "EXPORT_SCRIPT_SHA256=$(sha256sum "$0" | cut -d' ' -f1)"
  echo "SOURCE_PINS_SHA256=$(sha256sum "$HERE/gt_source.txt" | cut -d' ' -f1)"
  echo "EXPORT_OK=$n_ok"
  echo "EXPORT_FAIL=$got_fail"
} > "$OUT/PROVENANCE.txt"
(cd "$OUT" && find . -type f ! -name MANIFEST.sha256 -print0 | sort -z | xargs -0 sha256sum) > "$OUT/MANIFEST.sha256"
rc=0
[ "$n_ok" = "$EXP_OK" ] || { echo "EXPORT_COUNT_MISMATCH ok=$n_ok expected=$EXP_OK"; rc=1; }
[ "$got_fail" = "$want_fail" ] || { echo "EXPORT_FAIL_SET_MISMATCH got='$got_fail' expected='$want_fail'"; rc=1; }
exit $rc
