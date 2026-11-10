#!/usr/bin/env bash
# Integrity of the (possibly cached) ground truth before it is used: provenance must match the pins in
# gt_expected.txt and every file must match the manifest written at export time. Exits non-zero otherwise.
set -uo pipefail
OUT="${1:-gt_out}"
HERE="$(cd "$(dirname "$0")" && pwd)"
pin() { grep -E "^$1=" "$HERE/gt_expected.txt" | head -1 | cut -d= -f2-; }
prov() { grep -E "^$1=" "$OUT/PROVENANCE.txt" | head -1 | cut -d= -f2-; }
[ -f "$OUT/PROVENANCE.txt" ] && [ -f "$OUT/MANIFEST.sha256" ] || { echo "GT_VERIFY FAIL: no provenance/manifest"; exit 1; }
cat "$OUT/PROVENANCE.txt"
rc=0
[ "$(prov KICAD_SOURCE_SHA)" = "$(pin KICAD_SRC_SHA)" ] || { echo "GT_VERIFY FAIL: source sha"; rc=1; }
[ "$(prov KICAD_CLI)" = "$(pin KICAD_CLI_VERSION)" ] || { echo "GT_VERIFY FAIL: kicad version"; rc=1; }
[ "$(prov EXPORT_SCRIPT_SHA256)" = "$(sha256sum "$HERE/gt_export.sh" | cut -d' ' -f1)" ] || { echo "GT_VERIFY FAIL: export script changed"; rc=1; }
[ "$(prov EXPECTED_SHA256)" = "$(sha256sum "$HERE/gt_expected.txt" | cut -d' ' -f1)" ] || { echo "GT_VERIFY FAIL: pins changed"; rc=1; }
(cd "$OUT" && sha256sum --quiet -c MANIFEST.sha256) || { echo "GT_VERIFY FAIL: manifest"; rc=1; }
n=$(find "$OUT" -mindepth 2 -maxdepth 2 -name '*.xml' | wc -l)
[ "$n" = "$(pin EXPECT_EXPORT_OK)" ] || { echo "GT_VERIFY FAIL: $n netlists, expected $(pin EXPECT_EXPORT_OK)"; rc=1; }
[ $rc = 0 ] && echo "GT_VERIFY OK files=$(wc -l < "$OUT/MANIFEST.sha256") projects=$n"
exit $rc
