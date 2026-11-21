| variant | chosen as | light | dark | checker | mid |
|---|---|---|---|---|---|
| `plain-ink` | page-specific | pass | FAIL | pass | pass |
| `plain-paper` | page-specific | FAIL | pass | FAIL | pass |
| `mid` | generic-transparent | pass | pass | pass | FAIL |
| `fine` | text-fallback | FAIL | FAIL | FAIL | FAIL |
| `hair` | text-fallback | FAIL | FAIL | FAIL | FAIL |
| `thread` | text-fallback | FAIL | FAIL | FAIL | FAIL |
| `halo` | page-specific | FAIL | pass | FAIL | pass |
| `duplex` | generic-transparent | pass | pass | pass | FAIL |
| `badge` | generic-with-own-backing | pass | pass | pass | FAIL |
| `erasure-light` | page-specific | FAIL | pass | FAIL | FAIL |
| `erasure-dark` | page-specific | pass | FAIL | pass | FAIL |

| variant | residual failure reasons |
|---|---|
| `plain-ink` | approved:decisive_below_contrast, approved:ink_below_contrast, approved_vs_rejected_gray_rms_below_floor, pending:decisive_below_contrast, pending:ink_below_contrast, rejected:decisive_below_contrast, rejected:ink_below_contrast |
| `plain-paper` | approved:decisive_below_contrast, approved:ink_below_contrast, approved_vs_rejected_gray_rms_below_floor, pending:decisive_below_contrast, pending:ink_below_contrast, rejected:decisive_below_contrast, rejected:ink_below_contrast |
| `mid` | approved:decisive_below_contrast, approved:ink_below_contrast, approved_vs_rejected_gray_rms_below_floor, pending:decisive_below_contrast, pending:ink_below_contrast, rejected:decisive_below_contrast, rejected:ink_below_contrast |
| `fine` | approved:decisive_below_contrast, approved:ink_below_contrast, approved_vs_rejected_gray_rms_below_floor, pending:decisive_below_contrast, pending:ink_below_contrast, rejected:decisive_below_contrast, rejected:decisive_crispness_below_floor, rejected:ink_below_contrast |
| `hair` | approved:decisive_below_contrast, approved:decisive_crispness_below_floor, approved:ink_below_contrast, approved_vs_rejected_gray_rms_below_floor, pending:decisive_below_contrast, pending:ink_below_contrast, rejected:decisive_below_contrast, rejected:decisive_crispness_below_floor, rejected:ink_below_contrast |
| `thread` | approved:decisive_below_contrast, approved:decisive_crispness_below_floor, approved:ink_below_contrast, approved_vs_rejected_gray_rms_below_floor, pending:decisive_below_contrast, pending:ink_below_contrast, rejected:decisive_below_contrast, rejected:decisive_crispness_below_floor, rejected:ink_below_contrast |
| `halo` | pending:decisive_below_contrast, pending:erasure_probe_confirms_decisive_erased |
| `duplex` | pending:decisive_below_contrast |
| `badge` | pending:decisive_below_contrast, pending:erasure_probe_confirms_decisive_erased |
| `erasure-light` | approved:decisive_below_contrast, approved:erasure_probe_confirms_decisive_erased, approved:ink_below_contrast, pending:decisive_below_contrast, pending:erasure_probe_confirms_decisive_erased, pending:ink_below_contrast, rejected:decisive_below_contrast, rejected:erasure_probe_confirms_decisive_erased, rejected:ink_below_contrast |
| `erasure-dark` | approved:decisive_below_contrast, approved:erasure_probe_confirms_decisive_erased, approved:ink_below_contrast, pending:decisive_below_contrast, pending:erasure_probe_confirms_decisive_erased, pending:ink_below_contrast, rejected:decisive_below_contrast, rejected:erasure_probe_confirms_decisive_erased, rejected:ink_below_contrast |

| status | page | variant | mode | ink | decisive | erasure inside mean |
|---|---|---|---|---|---|---|
| pending | dark | `plain-ink` | whole-icon-lost | 1.01 | 1.01 | 3.75 |
| approved | dark | `plain-ink` | whole-icon-lost | 1.01 | 1.01 | 3.23 |
| rejected | dark | `plain-ink` | whole-icon-lost | 1.01 | 1.01 | 2.92 |
| pending | light | `plain-paper` | whole-icon-lost | 1.05 | 1.05 | 7.5 |
| approved | light | `plain-paper` | whole-icon-lost | 1.05 | 1.05 | 6.46 |
| rejected | light | `plain-paper` | whole-icon-lost | 1.05 | 1.05 | 5.84 |
| pending | checker | `plain-paper` | whole-icon-lost | 1.16 | 1.16 | 11.72 |
| approved | checker | `plain-paper` | whole-icon-lost | 1.05 | 1.12 | 9.06 |
| rejected | checker | `plain-paper` | whole-icon-lost | 1.16 | 1.05 | 5.84 |
| pending | mid | `mid` | whole-icon-lost | 1.0 | 1.0 | 0.0 |
| approved | mid | `mid` | whole-icon-lost | 1.0 | 1.0 | 0.0 |
| rejected | mid | `mid` | whole-icon-lost | 1.0 | 1.0 | 0.0 |
| rejected | light | `fine` | decisive-diluted-by-subpixel | 17.89 | 7.91 | 153.93 |
| pending | dark | `fine` | whole-icon-lost | 1.01 | 1.01 | 3.75 |
| approved | dark | `fine` | whole-icon-lost | 1.01 | 1.01 | 3.3 |
| rejected | dark | `fine` | whole-icon-lost | 1.01 | 1.01 | 2.58 |
| rejected | checker | `fine` | decisive-diluted-by-subpixel | 14.59 | 7.91 | 153.93 |
| rejected | mid | `fine` | decisive-lost-unexplained | 3.85 | 2.9 | 61.83 |
| rejected | light | `hair` | decisive-diluted-by-subpixel | 17.89 | 7.91 | 153.93 |
| pending | dark | `hair` | whole-icon-lost | 1.01 | 1.01 | 3.75 |
| approved | dark | `hair` | whole-icon-lost | 1.01 | 1.01 | 3.43 |
| rejected | dark | `hair` | whole-icon-lost | 1.01 | 1.01 | 2.58 |
| rejected | checker | `hair` | decisive-diluted-by-subpixel | 14.59 | 7.91 | 153.93 |
| rejected | mid | `hair` | decisive-lost-unexplained | 3.85 | 2.9 | 61.83 |
| approved | light | `thread` | decisive-diluted-by-subpixel | 7.91 | 7.91 | 185.11 |
| pending | dark | `thread` | whole-icon-lost | 1.01 | 1.01 | 3.75 |
| approved | dark | `thread` | whole-icon-lost | 1.01 | 1.01 | 3.1 |
| rejected | dark | `thread` | whole-icon-lost | 1.01 | 1.01 | 3.75 |
| approved | checker | `thread` | decisive-diluted-by-subpixel | 7.91 | 7.91 | 179.48 |
| approved | mid | `thread` | whole-icon-lost | 2.9 | 2.9 | 74.35 |
| pending | light | `halo` | decisive-erased | 16.97 | 1.41 | 8.0 |
| pending | checker | `halo` | decisive-erased | 16.97 | 1.41 | 12.5 |
| pending | mid | `duplex` | decisive-lost-unexplained | 16.97 | 1.18 | 131.0 |
| pending | mid | `badge` | decisive-erased | 16.97 | 1.18 | 0.0 |
| pending | light | `erasure-light` | decisive-erased | 4.65 | 1.19 | 0.0 |
| approved | light | `erasure-light` | decisive-erased | 4.65 | 1.0 | 3.85 |
| rejected | light | `erasure-light` | decisive-erased | 4.65 | 1.0 | 1.75 |
| pending | checker | `erasure-light` | decisive-erased | 4.65 | 1.19 | 11.72 |
| approved | checker | `erasure-light` | decisive-erased | 4.65 | 1.23 | 11.06 |
| rejected | checker | `erasure-light` | decisive-erased | 3.79 | 1.0 | 1.75 |
| pending | dark | `erasure-dark` | decisive-erased | 3.89 | 1.38 | 0.0 |
| approved | dark | `erasure-dark` | decisive-erased | 3.89 | 1.0 | 1.62 |
| rejected | dark | `erasure-dark` | decisive-erased | 3.89 | 1.0 | 2.79 |
| pending | mid | `erasure-dark` | whole-icon-lost | 1.0 | 2.83 | 93.73 |

one asset for all required pages: `mid` (worst decisive contrast 3.89)

| page | recommended | other variants that pass | fallback |
|---|---|---|---|
| light | `mid` | `plain-ink`, `duplex`, `badge`, `erasure-dark` | — |
| dark | `mid` | `plain-paper`, `halo`, `duplex`, `badge`, `erasure-light` | — |
| checker | `mid` | `plain-ink`, `duplex`, `badge`, `erasure-dark` | — |
