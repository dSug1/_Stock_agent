# m_share labelling — what it means + what's built (Stages A–B)

**Status:** decided D16; **Stage A built (D17)** = PIT fundamentals from SEC EDGAR; **Stage B built
(D18)** = the m_share decomposition is now the real panel label. Stages C–D (coverage + run the
kill-switch) are next. This note explains the idea, the mechanics as built, and the live result.

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
**positive** "ran-on-hype" label needs a big return **AND** `m_share ≥ 0.50` (⚙): the re-rating
dominated. This is the quantitative version of the Elicio-vs-TScan distinction.

**As built** (`src/hype_parser/mshare.py`): everything is **per-share**, so the split is an *exact
identity* in the inputs (no market-cap bookkeeping). `F = revenue_ttm / shares` (revenue per share),
`M = price / F` (the P/S multiple), `price = M × F`. We use the **same adjusted closes** as the
forward return for `price`, so `log(price_H/price_0) = log(M_H/M_0) + log(F_H/F_0)` holds exactly —
and **dilution shows up in `F`** (more shares → lower revenue per share → the re-rating had to do more
of the lifting, so `m_share` can exceed 1). The two endpoints' revenue+shares come from Stage A's
`fundamentals_as_of(ticker, t0)` and `…(ticker, t0+H)`. `m_share` is left undefined when the total
return is ≈ 0 (the denominator is ill-conditioned) — harmless, because a flat name is never a positive.

### The pre-revenue rule (decision D16)

Much of the panel is **pre-revenue biotech** (the CRISPR/mKRAS/Elicio cohort): no sales, no earnings,
so there's no fundamental to grow and P/S is undefined. By construction, *any* run in such a name is a
re-rating. So the rule is simply:

```
pre-revenue OR revenue <= $25M floor (t0)   ⇒   m_share := 1   (pure-narrative)
material revenue                            ⇒   decompose via P/S as above
```

This keeps the biotech cohort and the anchors in the panel (excluding them would turn the cross-sector
screener into a disguised software screener — contrary to the D1 premise). The **$25M materiality
floor** (D17 finding) is why milestone/collaboration revenue — e.g. CRSP's $3M as-of 2020 — is treated
as *no real fundamental*, not as a tiny denominator that would make P/S astronomical.

---

## 4. Where the fundamentals come from (SEC EDGAR, reused)

We need, for each panel name, its **revenue** and **share count** as they stood at `t0` — and we need
them **point-in-time**: the value as **first reported** in a filing dated **≤ t0**, never a later
restatement (Protocol §1), and present even for names that later **delisted** (or hard-negatives go
missing and the panel is survivorship-biased).

SEC EDGAR's **companyfacts** API is the ideal source. **As built** (`src/hype_parser/fundamentals.py`,
Stage A / D17):

- **Free + clean licensing** (public-domain filings) — also satisfies the repo's "switch off
  yfinance before deploy" constraint; EDGAR fundamentals are deploy-safe (yfinance prices are not).
- **Survivorship-free** — a company's filings persist after it delists.
- **Point-in-time** — companyfacts returns *every* reported period with its **filing date**, so
  `fundamentals_as_of(ticker, date)` picks the latest period whose **filing date ≤ date**. First-print,
  leak-free (Protocol §1) — a later 10-K/A restatement is invisible before its own filing date.
- **Why a 5_Hype-local module, not the literal `module_4c` import:** module_4c returns only the **8
  most-recent** periods and **drops the per-fact `filed` date**, and it hard-imports `layer_1.*`
  (which would break 5_Hype's standalone architecture, D3). So we **reused its GAAP-concept knowledge**
  (concept aliases + the TTM cumulative-YTD logic) and **added the `Revenues` tags**, in a
  self-contained, stdlib-only, injectable, fail-open client that stores the full history with filing
  dates. Schema **v7** (`company_facts`); CLI `scripts/5_fundamentals.py --fetch / --as-of`.
- **Validated live:** NTLA = pre-revenue as-of 2018-01, $52M revenue as-of 2023-06 (the snapshot
  evolves point-in-time); NTAP = $5.41B TTM / $24.38 rev-per-share as-of 2020-10 (matches NetApp's
  actual FY2020).

Alternatives considered and rejected (D16): **Sharadar SF1** (paid, turnkey) — adds cost + licensing;
**FMP/EOD** (cheap) — restated, not first-print → breaks the PIT rule; **defer m_share** (price-only) —
keeps the crude weakness. EDGAR is the only free + survivorship-free + PIT + clean-licensing option.

---

## 5. The staged build that follows (Phase 2 escalation)

```
Stage A  PIT fundamentals module   ✓ DONE (D17)  fundamentals.py; first-print (revenue,shares) with
                                                  filed-date discipline; as_of(ticker, date); schema v7
Stage B  m_share decomposition      ✓ DONE (D18)  mshare.py; split [t0,t0+H] into Δmultiple vs
                                                  Δfundamental; m_share=1 pre-revenue/sub-floor; the
                                                  REAL panel label (positive needs return>=hit AND
                                                  m_share>=0.5)
Stage C  panel to n>=100            … next        delisted-inclusive universe + temporal/sector/regime
                                                  stratification (Protocol §2.3) + more seed themes
                                                  (kills the 4-theme collinearity, D14 blocker 4)
Stage D  run the kill-switch        … then        on the real panel; pass -> gate Modules A/B
                                                  (Protocol §8); fail -> premise false -> stop
```

Stage C carries the last judgment calls (which delisted-ticker universe / price source, how many new
themes) — these get teed up before the n≥100 expansion.

---

## 6. The live result (Stage B, 57-row rebuild)

m_share is now the label authority. On the current crude panel the label modes are **22 decomposed /
32 pre_revenue / 3 no-fundamentals** (price-only fallback). All four +100% "doublers" had `m_share ≥
0.5` — i.e. **every nascent-theme name that doubled here did so via re-rating, not revenue growth** —
so the m_share clause is *non-binding on this small panel*. That is an honest finding, not a bug: the
clause is **tested-discriminating** (four decomposed names sit `< 0.5` and would be demoted if they
doubled) and will start *binding* at n≥100 (Stage C), when revenue-bearing "doublers" appear.

The **kill-switch read is unchanged** (`narrative_t ≈ −0.67`): it regresses the *continuous* forward
return, so m_share — which only sharpens the positive/hard_negative split — does not move it. The
remaining "no signal" stays a **power problem (n)**, which is Stage C's job.

## 7. What this does NOT do (yet)

The label + fundamentals plumbing is now real, but the panel is still n≈57 and **underpowered** — the
kill-switch keeps refusing a verdict, every ⚙ parameter stays unfit, and nothing here is a trading
signal until Stage D runs and passes. See `decisions.md` D16/D17/D18.
