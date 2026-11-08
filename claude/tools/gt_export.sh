#!/usr/bin/env bash
# Ground-truth export (M4.0). Runs on the CI runner: KiCad itself renders the PDF and computes the netlist,
# so the reference connectivity is independent of the ECCI algorithm. Nothing here is committed to the repo.
set -uo pipefail
# Immutable source snapshot captured by the initial M4.0 run; update only with a reviewed dataset revision.
SOURCE_COMMIT="${KICAD_SOURCE_COMMIT:-2e4136d0b1e8c76003df2b3095ba087b99b8408b}"
OUT="${1:-gt_out}"
mkdir -p "$OUT"
rm -rf ks
git clone --quiet --filter=blob:none --no-checkout https://github.com/KiCad/kicad-source-mirror.git ks || { echo "CLONE FAILED"; exit 2; }
git -C ks fetch --quiet --depth 1 origin "$SOURCE_COMMIT" || { echo "PINNED SOURCE FETCH FAILED: $SOURCE_COMMIT"; exit 2; }
git -C ks checkout --quiet --detach FETCH_HEAD || { echo "PINNED SOURCE CHECKOUT FAILED"; exit 2; }
git -C ks sparse-checkout set demos || { echo "SPARSE FAILED"; exit 2; }
echo "KICAD_SOURCE_SHA=$(git -C ks rev-parse HEAD)"
echo "KICAD_SOURCE_REQUESTED=$SOURCE_COMMIT"
echo "KICAD_CLI=$(kicad-cli version 2>&1 | head -1)"
n_ok=0; n_fail=0
while IFS= read -r pro; do
  d=$(dirname "$pro"); name=$(basename "$pro" .kicad_pro); sch="$d/$name.kicad_sch"
  if [ ! -f "$sch" ]; then echo "SKIP(no root sheet) $pro"; continue; fi
  tgt="$OUT/$name"; mkdir -p "$tgt/src"
  (cd "$d" && find . -name '*.kicad_sch' -print0 | xargs -0 -I{} cp --parents {} "$OLDPWD/$tgt/src/") 2>/dev/null
  ok=1
  kicad-cli sch export pdf --output "$tgt/$name.pdf" "$sch" >"$tgt/pdf.log" 2>&1 || { echo "FAIL pdf $name"; ok=0; }
  kicad-cli sch export netlist --format kicadxml --output "$tgt/$name.xml" "$sch" >"$tgt/net.log" 2>&1 || { echo "FAIL netlist $name"; ok=0; }
  if [ $ok = 1 ]; then n_ok=$((n_ok+1)); else n_fail=$((n_fail+1)); fi
done < <(find ks/demos -name '*.kicad_pro' | sort)
echo "EXPORT_OK=$n_ok EXPORT_FAIL=$n_fail"
