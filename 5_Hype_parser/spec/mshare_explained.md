# m_share labelling + the escalation plan — what it means (design note)

**Status:** decided 2026-06-26 (decision D16); **not built yet** — this note explains the approach
and the staged plan that follows, so the next session can build it without re-deriving the choices.

> Read `crude_panel_explained.md` first (the Phase-1 crude panel this escalates), then Protocol §2.1
> (the label definition) and §1 (the point-in-time cardinal rule).

---

## 1. In one sentence

`m_share` is the slice of a stock's return that came from the market **re-rating it** (paying more per
unit of business) rather than from the **business actually growing** — and a name only counts as a
"ran-on-hype" positive if that re-rating slice dominates. Computing it for a survivorship-free,
point-in-time panel is the one thing the crude panel skipped, and it needs historical fundamentals —
which we'll get **free** from SEC EDGAR.

---

## 2. Why this matters (the hype-vs-value-realization line)

The whole premise is that **narrative/attention** drives the move, not earnings. But a cheap stock
that triples because its revenue tripled isn't "hype" — it's ordinary value-realization. Protocol §2.1
calls the rule that separates them **non-negotiable**: a positive label requires not just a big
forward return, but that the return came mostly from **multiple expansion** (the re-rating), not from
fundamental growth.

The crude panel (D14/D15) used **price-only** labels and could not draw this line. That's the single
biggest reason its read is only indicative. `m_share` is what makes the label real.

---

## 3. How m_share works (plain mechanics)

A stock's price can always be written as **a multiple × a per-share fundamental**:

```
Price  =  (P/S multiple)  ×  (Sales per share)        # for revenue-bearing names
```

(P/E × EPS works the same way; we use P/S because most hype/growth names have revenue but not yet
earnings.) Over the holding window `[t0, t0+H]`, the total return splits cleanly (in logs it's exactly
additive):

```
log(Price_end / Price_start)  =  log(Multiple_end / Multiple_start)   ← the RE-RATING
                               +  log(Fundamental_end / Fundamental_start)   ← the GROWTH

m_share  =  log(Multiple_end / Multiple_start)  /  log(Price_end / Price_start)
```

`m_share` near **1** = almost all of the move was the market re-rating the name (narrative/attention).
`m_share` near **0** = the move was the business growing into its price (value-realization). A
**positive** "ran-on-hype" label needs a big return **AND** `m_share ≥` a threshold (⚙, ~0.5+): the
re-rating dominated. This is the quantitative version of the Elicio-vs-TScan distinction.

### The pre-revenue rule (decision D16)

Much of the panel is **pre-revenue biotech** (the CRISPR/mKRAS/Elicio cohort): no sales, no earnings,
so there's no fundamental to grow and P/S is undefined. By construction, *any* run in such a name is a
re-rating. So the rule is simply:

```
pre-revenue (no real revenue at t0)   ⇒   m_share := 1   (pure-narrative)
revenue-bearing                       ⇒   decompose via P/S as above
```

This keeps the biotech cohort and the anchors in the panel (excluding them would turn the cross-sector
screener into a disguised software screener — contrary to the D1 premise).

---

## 4. Where the fundamentals come from (SEC EDGAR, reused)

We need, for each panel name, its **revenue** and **share count** as they stood at `t0` — and we need
them **point-in-time**: the value as **first reported** in a filing dated **≤ t0**, never a later
restatement (Protocol §1), and present even for names that later **delisted** (or hard-negatives go
missing and the panel is survivorship-biased).

SEC EDGAR's **companyfacts** API is the ideal source and we already have the client:

- **Free + clean licensing** (public-domain filings) — also satisfies the repo's "switch off
  yfinance before deploy" constraint; EDGAR fundamentals are deploy-safe (yfinance prices are not).
- **Survivorship-free** — a company's filings persist after it delists.
- **Point-in-time** — companyfacts returns *every* reported period with its **filing date**, so to get
  the as-of-`t0` value we pick the latest period whose filing date ≤ `t0`. First-print, leak-free.
- **Already built** — `2_Funds_parser/src/module_4c/{edgar_client.py, fundamentals_db.py}` fetches
  companyfacts with conditional-GET caching, TTM correction for cumulative-YTD concepts, and XBRL-tag
  aliasing. We reuse it; the main addition is the `Revenues` concept and a PIT historical extractor.

Alternatives considered and rejected (D16): **Sharadar SF1** (paid, turnkey) — adds cost + licensing;
**FMP/EOD** (cheap) — restated, not first-print → breaks the PIT rule; **defer m_share** (price-only) —
keeps the crude weakness. EDGAR is the only free + survivorship-free + PIT + clean-licensing option.

---

## 5. The staged build that follows (Phase 2 escalation)

```
Stage A  PIT fundamentals module        reuse module_4c edgar_client; first-print (revenue, shares)
                                         series with filed-date discipline; as_of(ticker, date)
Stage B  m_share decomposition           split [t0, t0+H] return into Δmultiple vs Δfundamental
                                         (P/S where revenue exists; m_share=1 pre-revenue) → real label
Stage C  panel to n>=100                  delisted-inclusive universe + temporal/sector/regime
                                         stratification (Protocol §2.3) + more seed themes (kills the
                                         4-theme collinearity, D14 blocker 4)
Stage D  run the kill-switch              on the real panel; if it passes, gate Modules A/B behind it
                                         (Protocol §8); if it fails, the premise is false → stop
```

Stages A–B are decision-free and reuse existing infra. Stage C carries the last judgment calls (which
delisted-ticker universe, how many new themes) — to be surfaced when we get there.

---

## 6. What this does NOT do (yet)

No code is built for any of this — it is the agreed approach. Until Stage D runs and passes, every ⚙
parameter stays unfit, the kill-switch keeps refusing a verdict below n=100, and nothing here is a
trading signal. The crude harness (`panel_builder.py` + `--build-crude`) is the skeleton Stages A–C
extend. See `decisions.md` D16.
