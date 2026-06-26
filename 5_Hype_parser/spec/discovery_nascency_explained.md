# Discovery nascency gate — as-built note (plain language)

This note explains the **nascency gate / ranking** added to theme discovery (decision D29;
`src/hype_parser/discovery/nascency.py`). It is the step that turns a *pile of discovered themes* into
a *ranked shortlist* — newest-and-accelerating first. Read it after `discovery_explained.md` (the
discovery pipeline) and `decisions.md` D27–D29.

## The problem it solves

Convergence (D27) finds themes — groups where several independent expert juries agree. But it scores a
theme only by **how many** independent credible leading juries point at it. In the first live run all
five discovered themes had the **same** convergence score (1.6 = two leading juries each), so they were
indistinguishable. Yet they are *not* equally interesting: a theme the juries started naming **this
year and are naming more each year** is early (long runway); one they recognized a decade ago is late.
We need to rank by **how nascent and accelerating** the recognition is — not just how convergent.

## The idea (one paragraph)

Each jury signal carries a **year** (the edition it came from). So a discovered theme has a **timeline**
— how many jury recognitions it collected per year. From that timeline we read three things and multiply
them into a rank:

1. **Convergence** — how many independent credible *leading* juries agree (the D27 score). Reused as-is.
2. **Acceleration** — is jury attention *rising*? Measured as `beta_jury`, the **OLS slope of
   ln(1 + jury_count) over the recent window of years**. This is literally the **same `beta_spec`
   instrument the diffusion engine uses** for the document corpus — here applied to annual jury counts.
   Positive β = the juries are naming the theme *more* each year (still emerging); negative = fading.
3. **Recency** — what share of the theme's signals landed in the last few years. High = fresh.

```
rank_score = convergence_score × (recency_floor + recency) × (1 + max(0, beta_jury))
```

The `recency_floor` keeps a high-convergence theme with sparse years from collapsing to zero. Every
weight is an open ⚙ knob in `config/discovery.yaml` (`nascency:`), unfit until the back-test.

## What it does, mechanically

- `theme_year_counts(theme)` — `{year: n_signals}` for a theme's leading-jury signals (year-less
  snapshot signals simply don't appear; the YC/Nobel feeds carry real years).
- `nascency_metrics(counts)` — fills gaps so the slope window is **real contiguous years** (a missing
  year is a real 0, not a skipped point — the same discipline as the diffusion engine's contiguous
  months), then computes `beta_jury`, `accelerating`, `first_year`, `years_since_first`, `recency`.
- `rank_discovered(cfg)` — for every discovered theme, recomputes the convergence score
  (`convergence.score_group`) **and** the nascency metrics, multiplies them into `rank_score`, and
  returns the themes sorted best-first. It is a **pure read** — nothing is fetched, it recomputes from
  the stored `jury_signals` (the diffusion "compute-on-read" pattern; the raw signal years are the
  source of truth, the slope is derived at read time).
- **Refined horizon** — the runway estimate (spec §5) was tier-only in D27 ("which jury positions
  fired"). Now it is **nudged within its tier band by the timeline**: all-recent + accelerating ⇒
  toward the long (early) end; old / decelerating ⇒ toward the short end. `persist_refined_horizon`
  optionally writes it back to `themes.horizon_years` with `horizon_confidence='timeline'`.

## What the live run showed

Ranking the five themes (all convergence 1.6) by the gate spreads them out by their dynamics:

```
 rank  conv b_jury recency ~runway  theme
 2.49   1.6   0.31    0.69    4.1y   …drones          (accelerating, fresh → early, long runway)
 2.40   1.6   0.00    1.00    3.5y   …nuclear power
 1.90   1.6   0.04    0.64    3.9y   …AI chatbots…
 1.67   1.6   0.00    0.54    3.6y   …AI and math
 1.37   1.6  -0.14    0.36    2.5y   …AI in 2026      (decelerating, broad → later-stage, short runway)
```

The big "AI in 2026" mega-cluster — which `tau` over-merged — correctly sinks to the bottom: its jury
attention is *decelerating* and *less recent*, so the gate reads it as later-stage. "Drones," fresh and
accelerating, rises to the top with the longest runway. The horizon now varies with the data (2.5–4.1y)
instead of being a flat 3.5y tier default.

## Limits (honest)

- **`beta_jury` is coarse on sparse annual data** — a theme with one or two years of signals has a
  noisy slope. Treat it as a tilt, not a precise growth rate; the back-test will calibrate the window
  and weights.
- **It ranks, it does not yet gate.** Nothing is *dropped* for low nascency — that threshold belongs
  with the §9 back-test, alongside `tau`. For now `--rank` orders; it doesn't filter.
- **Jury timeline vs corpus curve — now both exist.** This gate is the lighter, always-available
  signal; the corpus `β_spec`/`p_main` via the diffusion engine (D30, `diffusion_bridge.py`) is the
  heavier one. They are complementary — fusing them into one rank is a follow-up.

## Run it

```
scripts/5_discovery.py --rank                       # rank discovered themes by the nascency gate
scripts/5_discovery.py --rank --persist-horizon     # also write the timeline-refined horizon to themes
scripts/5_discovery.py --rank --current-year 2024   # pin the reference year (default: now)
```

Files: `src/hype_parser/discovery/nascency.py`; config `config/discovery.yaml::nascency`; tests in
`tests/test_discovery.py`.
