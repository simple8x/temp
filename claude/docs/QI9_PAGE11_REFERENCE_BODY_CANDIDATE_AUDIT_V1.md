# QI9 page 11 reference-to-body candidate audit v1

- Source PDF SHA-256: `a7860fba0cf7ea30e7c209b0ba7405481edfffd2abdcfffa00b465e08b2d45fc`
- Method: OCR label bbox centroid → nearest BCX56 body-cluster centroid.
- Authority: candidate-only; no reference binding or connectivity promotion.

## Candidate map
- `Q2` → body cluster `27` (candidate only)
- `Q3` → body cluster `57` (candidate only)
- `Q5` → body cluster `58` (candidate only)
- `Q6` → body cluster `59` (candidate only)
- `Q7` → body cluster `60` (candidate only)
- `Q8` → body cluster `24` (candidate only)

## Conflicts / gaps
- `Q3` / `Q5`: one OCR pass reads Q3 at cluster 57 position; another reads Q5 at the same position.
- `Q5` also appears at a second position near cluster 58. Do not choose automatically.
- `Q4` and `Q9`: no reliable reference-label observation in current OCR evidence.

## Evidence constraints
- OCR passes share Tesseract and the same source PDF; they are correlated, not independent ground truth.
- Nearest-centroid matching is spatial proximity only.
- BCX56 pin-position confirmation does not establish electrical connectivity.
- `VERIFIED_CONNECTED=0`; `VERIFIED_SEPARATE=0`; no promotion performed.

## Observations
- ocr_psm11_3x: `Q2` conf=90.541595 bbox=[349.667, 143.333, 356.0, 147.667] → cluster 27 (15.05 pt)
- ocr_psm11_3x: `Q3` conf=88.831032 bbox=[598.0, 146.667, 604.667, 151.333] → cluster 57 (15.17 pt)
- ocr_psm11_3x: `Q5` conf=71.074905 bbox=[598.0, 222.333, 604.667, 227.0] → cluster 58 (15.14 pt)
- ocr_psm11_3x: `Q6` conf=89.278465 bbox=[349.667, 287.333, 356.0, 291.667] → cluster 59 (15.04 pt)
- ocr_psm11_3x: `Q7` conf=80.516777 bbox=[598.0, 290.667, 604.667, 295.333] → cluster 60 (15.16 pt)
- ocr_psm6_6x: `Q5` conf=73.161911 bbox=[598.167, 146.833, 604.5, 151.333] → cluster 57 (15.15 pt)
- ocr_psm6_6x: `Q7` conf=87.876198 bbox=[598.167, 290.833, 604.667, 295.333] → cluster 60 (15.22 pt)
- ocr_body_psm6_6x: `Q5` conf=73.161911 bbox=[598.167, 146.833, 604.5, 151.333] → cluster 57 (15.15 pt)
- ocr_body_psm6_6x: `Q7` conf=87.876198 bbox=[598.167, 290.833, 604.667, 295.333] → cluster 60 (15.22 pt)
- probe_8x_psm6: `Q2` conf=85.3 bbox=[349.75, 143.25, 356.12, 147.75] → cluster 27 (15.15 pt)
- probe_8x_psm6: `Q5` conf=86.7 bbox=[598.12, 222.5, 604.5, 226.88] → cluster 58 (15.11 pt)
- probe_8x_psm11: `Q2` conf=92.1 bbox=[349.75, 143.25, 356.12, 147.75] → cluster 27 (15.15 pt)
- probe_8x_psm11: `Q3` conf=91.9 bbox=[598.12, 146.88, 604.5, 151.38] → cluster 57 (15.12 pt)
- probe_8x_psm11: `Q8` conf=91.8 bbox=[349.75, 359.25, 356.12, 363.62] → cluster 24 (15.14 pt)
- probe_8x_psm12: `Q2` conf=92.1 bbox=[349.75, 143.25, 356.12, 147.75] → cluster 27 (15.15 pt)
- probe_8x_psm12: `Q8` conf=91.8 bbox=[349.75, 359.25, 356.12, 363.62] → cluster 24 (15.14 pt)
[exit status 0]