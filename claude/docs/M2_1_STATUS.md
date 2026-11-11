# M2.1 status (text/glyph filter + distance-based cluster merge)

## Changes
- classify.py: TEXT = small connected component touching nothing AND member of a valid text row
  (>=3 aligned similar comps with >=2 two-dimensional, or a pair with equal height/baseline within 0.15).
  Text is excluded from the wire-style population. Evidence: GLYPH_SHAPE + GLYPH_ROW (independent lineage).
- symbols.py: clusters merge when bbox gap <= 0.5 x larger dimension of the SMALLER part (single-link fixpoint).
- pins.py: PinParams.cluster_gap_frac.

## Evidence (local; CI pending until pushed)
- Full suite: 238 passed. Mutation check: 29/29 killed (M23-M29 new; M25, M29 first SURVIVED,
  tests t11-t14 added, no assertion relaxed, then KILLED).
- Isolation gate: PASS (3 commits, 0 violations).

## Real PDF ([QI9-2604-A01] Module IO.pdf) -- observation only, NO ground truth
- Page 4: 69 prims -> BORDER 1, BODY 32, OTHER 18, WIRE 18.
- Page 10: 11031 prims -> TEXT 8694 (951 glyph comps, 215 rows), BODY 1360, WIRE 974, BORDER 1, OTHER 2; classify 8.7 s.

## Known limits (UNKNOWN/ASSUMPTION)
- Words with 2 glyphs incl. a 1-D glyph (e.g. "IN") are not TEXT (test t10 documents it).
- Isolated hyphens stay WIRE. One LED emission arrow on a real page is still TEXT -> cluster matches nothing.
- Junction dots are BODY clusters (noise; role belongs to M3). S4 (text pin numbers) not implemented.
- Fixture library hand-written (ASSUMPTION), synthetic tests partly circular; eps uncalibrated;
  clip paths/optional content not applied. TEXT count on page 10 is not validated against a real text count.
