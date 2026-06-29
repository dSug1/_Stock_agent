# Acrivon-Pattern Screener — Seed Validation (§13)

*Run `2026-06-29T16:04:01+00:00` · generated 2026-06-29 · decision threshold composite ≥ 0.6.*

> ℹ️ **Graduated (accepted, D7):** positive(s) re-rated ABOVE the ceiling and cut by design: **TNGX** — out-of-scope graduates, not a failure.

## Headline

- **Precision** 1.000 · **Recall** 1.000 · **F1** 1.000  *(over scored positive/negative seeds, TP=2 FP=0 FN=0 TN=3)*
- Positives: 4 (2 scored) · Negatives: 8 (0 scored) · Borderline: 3
- Positives lost before scoring: 0

## Per-stage survival

| Group | n | in store | deleted | tagged | w/ evidence | scored | triage-killed | pred. positive |
|:--|--:|--:|--:|--:|--:|--:|--:|--:|
| positive | 4 | 3 | 1 | 1 | 2 | 2 | 0 | 2 |
| negative | 8 | 3 | 5 | 0 | 0 | 0 | 3 | 0 |
| borderline | 3 | 2 | 1 | 1 | 2 | 2 | 0 | 1 |

## Per-seed detail

| Ticker | Label | Status | Tier | Composite | Stage/Reason | Name |
|:--|:--|:--|--:|--:|:--|:--|
| **ACRV** | positive | scored | 1 | 0.968 |  | Acrivon Therapeutics |
| **BOLD** | positive | scored | 1 | 0.720 |  | Boundless Bio |
| **IDYA** | positive | unscored | 0 | — |  | IDEAYA Biosciences |
| **TNGX** | positive | deleted | — | — | stage0b · mktcap_out_of_band (graduated $5,120M) | Tango Therapeutics |
| **RLAY** | borderline | deleted | — | — | stage0b · mktcap_out_of_band (too-large $4,040M) | Relay Therapeutics |
| **RXRX** | borderline | scored | 2 | 0.744 |  | Recursion Pharmaceuticals |
| **SDGR** | borderline | scored | 2 | 0.568 |  | Schrodinger |
| **A** | negative | deleted | — | — | stage0b · mktcap_out_of_band (too-large $38,414M) | Agilent Technologies |
| **BRKR** | negative | deleted | — | — | stage0b · mktcap_out_of_band (too-large $9,272M) | Bruker |
| **CRL** | negative | deleted | — | — | stage0b · mktcap_out_of_band (too-large $10,392M) | Charles River Laboratories |
| **ICLR** | negative | deleted | — | — | stage0b · mktcap_out_of_band (too-large $13,012M) | ICON plc |
| **MEDP** | negative | deleted | — | — | stage0b · mktcap_out_of_band (too-large $15,053M) | Medpace Holdings |
| **MRVI** | negative | triage_killed | 2 | — |  | Maravai LifeSciences |
| **NEOG** | negative | triage_killed | 3 | — |  | Neogen |
| **TKNO** | negative | triage_killed | 1 | — |  | Alpha Teknova |
