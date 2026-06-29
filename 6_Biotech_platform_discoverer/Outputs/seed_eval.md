# Acrivon-Pattern Screener — Seed Validation (§13)

*Run `2026-06-29T04:15:40+00:00` · generated 2026-06-29 · decision threshold composite ≥ 0.6.*

> ℹ️ **Graduated (accepted, D7):** positive(s) re-rated ABOVE the ceiling and cut by design: **TNGX, IDYA** — out-of-scope graduates, not a failure.

## Headline

- **Precision** 1.000 · **Recall** 1.000 · **F1** 1.000  *(over scored positive/negative seeds, TP=1 FP=0 FN=0 TN=0)*
- Positives: 4 (1 scored) · Negatives: 5 (0 scored) · Borderline: 3
- Positives lost before scoring: 0

## Per-stage survival

| Group | n | in store | deleted | tagged | w/ evidence | scored | pred. positive |
|:--|--:|--:|--:|--:|--:|--:|--:|
| positive | 4 | 2 | 2 | 1 | 2 | 1 | 1 |
| negative | 5 | 0 | 5 | 0 | 0 | 0 | 0 |
| borderline | 3 | 2 | 1 | 1 | 1 | 2 | 2 |

## Per-seed detail

| Ticker | Label | Status | Tier | Composite | Stage/Reason | Name |
|:--|:--|:--|--:|--:|:--|:--|
| **ACRV** | positive | scored | 1 | 0.904 |  | Acrivon Therapeutics |
| **BOLD** | positive | unscored | 1 | — |  | Boundless Bio |
| **IDYA** | positive | deleted | — | — | stage0b · mktcap_out_of_band (graduated $3,537M) | IDEAYA Biosciences |
| **TNGX** | positive | deleted | — | — | stage0b · mktcap_out_of_band (graduated $5,124M) | Tango Therapeutics |
| **RLAY** | borderline | deleted | — | — | stage0b · mktcap_out_of_band (too-large $4,045M) | Relay Therapeutics |
| **RXRX** | borderline | scored | 2 | 0.744 |  | Recursion Pharmaceuticals |
| **SDGR** | borderline | scored | 2 | 0.672 |  | Schrodinger |
| **A** | negative | deleted | — | — | stage0b · mktcap_out_of_band (too-large $38,414M) | Agilent Technologies |
| **BRKR** | negative | deleted | — | — | stage0b · mktcap_out_of_band (too-large $9,272M) | Bruker |
| **CRL** | negative | deleted | — | — | stage0b · mktcap_out_of_band (too-large $10,392M) | Charles River Laboratories |
| **ICLR** | negative | deleted | — | — | stage0b · mktcap_out_of_band (too-large $13,012M) | ICON plc |
| **MEDP** | negative | deleted | — | — | stage0b · mktcap_out_of_band (too-large $15,053M) | Medpace Holdings |
