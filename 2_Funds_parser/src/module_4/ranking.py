"""Module 4b — incremental price fetch + ratio matrix + archetype rank.

Reads survivors_{quarter}.parquet from Module 4a, fetches/updates prices for
each survivor in `data/prices.db`, computes the 10-feature ratio matrix per
ticker, matches against archetypes, ranks by composite_score = score *
match_confidence, and writes:

    _intermediate_outputs/ranked_candidates_{quarter}.parquet
    _intermediate_outputs/young_ticker_excluded_{quarter}.parquet  (if any)
    Outputs/ranking_report_{quarter}.html
    Outputs/ranking_report_{quarter}.xlsx

CLI exposes --rerank-only to skip the price fetch when only configs changed.
"""
from __future__ import annotations

import datetime as dt
import html
import logging
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from module_1 import ConfigError, PROJECT_ROOT, PipelineConfig, ensure_dir, resolve_quarter

from . import prices as price_cache
from .archetypes import load_archetypes, match_archetypes
from .ratios import INTER_WINDOW, WINDOWS_WEEKS, compute_ratios_for_ticker

log = logging.getLogger(__name__)


# ─── Config loading ──────────────────────────────────────────────────────────

def load_ranking_config(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"ranking config not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        loc = f"{path}:{mark.line + 1}:{mark.column + 1} " if mark else f"{path} "
        raise ConfigError(f"{loc}YAML syntax error: {e}") from e
    if not isinstance(raw, dict) or "ranking" not in raw:
        raise ConfigError(f"{path}: top-level 'ranking' key required")
    return raw["ranking"]


# ─── Output column ordering ──────────────────────────────────────────────────

_RATIO_COLS: list[str] = list(WINDOWS_WEEKS.keys()) + [k for (k, _, _) in INTER_WINDOW]
_PRICE_DATE_COLS: list[str] = [f"price_source_date_{k[2:]}w" for k in WINDOWS_WEEKS]


def _ranked_columns(survivor_cols: list[str], horizons: list[str]) -> list[str]:
    """Final column order for ranked_candidates_{quarter}.parquet.

    Dual-horizon columns: for each horizon H, emit `score_<H>` and
    `composite_<H>`. Plus `best_horizon` tag and `composite_best` sort key.
    """
    score_cols = [f"score_{h}" for h in horizons]
    composite_cols = [f"composite_{h}" for h in horizons]
    leading = (
        ["rank", "ticker", "name_of_issuer", "archetype"]
        + score_cols
        + ["match_confidence"]
        + composite_cols
        + ["best_horizon", "composite_best"]
    )
    ratios = list(_RATIO_COLS)
    prices = ["price_today"] + _PRICE_DATE_COLS + [
        "weeks_of_history_used", "young_ticker_flag",
    ]
    seen = set(leading + ratios + prices)
    passthrough = [c for c in survivor_cols if c not in seen]
    return leading + ratios + prices + passthrough


# ─── Main orchestrator ───────────────────────────────────────────────────────

def rank_universe(
    config: PipelineConfig,
    quarter: Optional[str] = None,
    *,
    rerank_only: bool = False,
) -> pd.DataFrame:
    """End-to-end Module 4b. Returns the ranked DataFrame."""
    quarter = quarter or resolve_quarter(config)

    survivors_path = (
        config.paths.intermediate_outputs_dir / f"survivors_{quarter}.parquet"
    )
    if not survivors_path.exists():
        raise FileNotFoundError(
            f"Survivors not found at {survivors_path}. "
            "Run scripts/4_run_hard_filters.py first."
        )
    survivors = pd.read_parquet(survivors_path)
    log.info("Loaded %d survivors from %s", len(survivors), survivors_path)
    if survivors.empty:
        log.warning("No survivors; writing empty ranked output and exiting.")
        return _write_empty_ranking(survivors, quarter, config)

    archetypes_yaml = PROJECT_ROOT / "config" / "archetypes.yaml"
    ranking_yaml = PROJECT_ROOT / "config" / "ranking.yaml"
    archetypes = load_archetypes(archetypes_yaml)
    rcfg = load_ranking_config(ranking_yaml)

    db_path = config.paths.prices_db
    price_cache.init_prices_db(db_path)

    # 1. Incremental price fetch (skipped when rerank_only=True)
    tickers = sorted(set(survivors["ticker"].dropna().astype(str).tolist()))
    if not rerank_only:
        price_cache.configure_rate_limits(
            info_rate_per_s=float(rcfg.get("fetch_rate_per_s", 5.0)),
            bars_rate_per_s=float(rcfg.get("fetch_rate_per_s", 5.0)),
        )
        statuses = price_cache.fetch_incremental_prices(
            tickers,
            db_path,
            cold_start_period=str(rcfg.get("cold_start_period", "400d")),
            batch_size=int(rcfg.get("fetch_batch_size", 100)),
            retries=int(rcfg.get("retries", 2)),
            backoff_s=tuple(rcfg.get("retry_backoff_s", [1.0, 2.0, 4.0])),
        )
        ok = sum(1 for s in statuses.values() if s == "ok")
        log.info("Price fetch: %d ok / %d total", ok, len(statuses))
    else:
        log.info("--rerank-only: skipping price fetch.")

    # 2. Compute ratios
    reference_date = pd.Timestamp(dt.datetime.utcnow().date())
    flat_fill = bool(rcfg.get("young_ticker_flat_fill", True))
    tol = int(rcfg.get("window_tolerance_trading_days", 3))
    min_weeks = int(rcfg.get("require_min_history_weeks", 12))
    min_conf = float(rcfg.get("min_confidence", 0.70))

    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        feature_rows: list[dict] = []
        for t in tickers:
            feats = compute_ratios_for_ticker(
                conn, t, reference_date,
                tolerance_trading_days=tol,
                flat_fill=flat_fill,
            )
            feature_rows.append(feats)

    feats_df = pd.DataFrame(feature_rows)
    log.info("Computed ratios for %d tickers", len(feats_df))

    # 3. Apply require_min_history_weeks (excluded -> audit file, not ranked)
    excluded_mask = (
        feats_df["weeks_of_history_used"].fillna(0).astype(int) < min_weeks
    )
    if not flat_fill:
        # When flat_fill is False, also exclude rows that hit a missing window.
        excluded_mask |= feats_df["young_ticker_flag"].fillna(False).astype(bool)
    excluded_tickers = feats_df.loc[excluded_mask, "ticker"].tolist()
    if excluded_tickers:
        log.info("Excluding %d young/short-history tickers from ranking",
                 len(excluded_tickers))
        excluded_path = (
            config.paths.intermediate_outputs_dir
            / f"young_ticker_excluded_{quarter}.parquet"
        )
        excluded_df = feats_df[excluded_mask].merge(
            survivors, on="ticker", how="left"
        )
        excluded_df["quarter"] = quarter
        excluded_df.to_parquet(excluded_path, index=False, engine="pyarrow")

    rankable = feats_df[~excluded_mask].copy().reset_index(drop=True)

    # Resolve active horizons. Default: both.
    horizons_cfg = rcfg.get("horizons", ["3mo", "12mo"])
    if not isinstance(horizons_cfg, list) or not horizons_cfg:
        raise ConfigError("ranking.horizons must be a non-empty list of horizon names")
    horizons: list[str] = [str(h) for h in horizons_cfg]
    # Validate every active horizon is present in every archetype's scores.
    for name, spec in archetypes.items():
        missing = [h for h in horizons if h not in spec["scores"]]
        if missing:
            raise ConfigError(
                f"archetype '{name}' missing scores for horizon(s) {missing}; "
                f"ranking.yaml::horizons = {horizons}"
            )

    # 4. Archetype matching (one pass; scores dict carries all horizons)
    arch_names: list[str] = []
    arch_scores_list: list[dict] = []
    arch_confs: list[float] = []
    for _, r in rankable.iterrows():
        name, scores, conf = match_archetypes(
            r.to_dict(), archetypes, min_confidence=min_conf
        )
        arch_names.append(name)
        arch_scores_list.append(scores)
        arch_confs.append(conf)
    rankable["archetype"] = arch_names
    rankable["match_confidence"] = arch_confs

    # Score-floor weighting: composite_H = score_H * (alpha + (1 - alpha) * confidence).
    # alpha=0 reproduces the legacy pure-multiplication formula; alpha=1 ignores
    # confidence (gate-only). Default 0.7 mirrors min_confidence so score class
    # dominates and confidence becomes the within-class tiebreaker. D19 holds
    # independently per horizon.
    alpha = float(rcfg.get("confidence_floor_weight", 0.7))
    if not 0.0 <= alpha <= 1.0:
        raise ConfigError(
            f"ranking.confidence_floor_weight must be in [0, 1], got {alpha}"
        )
    conf_mult = alpha + (1.0 - alpha) * rankable["match_confidence"]
    for h in horizons:
        col_score = f"score_{h}"
        col_comp = f"composite_{h}"
        # 0 for unclassified (empty scores dict), else the archetype's int.
        rankable[col_score] = [
            int(s.get(h, 0)) if s else 0 for s in arch_scores_list
        ]
        rankable[col_comp] = rankable[col_score].astype(float) * conf_mult

    # Per-ticker best_horizon = argmax(composite_H). Ties -> "equal".
    composite_h_cols = [f"composite_{h}" for h in horizons]
    if len(horizons) == 1:
        rankable["best_horizon"] = horizons[0]
        rankable["composite_best"] = rankable[composite_h_cols[0]]
    else:
        comp_arr = rankable[composite_h_cols].to_numpy()
        max_vals = comp_arr.max(axis=1)
        best_idx = comp_arr.argmax(axis=1)
        # Flag ties as "equal" (every horizon composite equal to max).
        all_equal = (comp_arr == max_vals[:, None]).all(axis=1)
        rankable["best_horizon"] = [
            "equal" if tied else horizons[i]
            for tied, i in zip(all_equal, best_idx)
        ]
        rankable["composite_best"] = max_vals

    if not bool(rcfg.get("include_unclassified", True)):
        rankable = rankable[rankable["archetype"] != "unclassified"].copy()

    # 5. Merge passthrough columns from survivors and rank
    merged = rankable.merge(survivors, on="ticker", how="left")
    merged["quarter"] = quarter

    sort_keys, sort_asc = _resolve_tiebreakers(
        rcfg.get("tiebreakers", ["fund_count_desc", "ticker_asc"])
    )
    merged = merged.sort_values(
        by=["composite_best", *sort_keys],
        ascending=[False, *sort_asc],
        na_position="last",
        kind="stable",
    ).reset_index(drop=True)
    merged.insert(0, "rank", merged.index + 1)

    top_n = rcfg.get("output_top_n")
    if top_n is not None and isinstance(top_n, int) and top_n > 0:
        merged = merged.head(top_n).copy()

    # 6. Final column ordering
    final_cols = _ranked_columns(list(survivors.columns), horizons)
    final_cols = [c for c in final_cols if c in merged.columns]
    merged = merged[final_cols]

    out_path = (
        config.paths.intermediate_outputs_dir / f"ranked_candidates_{quarter}.parquet"
    )
    merged.to_parquet(out_path, index=False, engine="pyarrow")
    log.info("Wrote ranked candidates -> %s", out_path)

    # 7. Reports
    html_path = config.paths.reports_dir / f"ranking_report_{quarter}.html"
    xlsx_path = config.paths.reports_dir / f"ranking_report_{quarter}.xlsx"
    generate_ranking_report_html(merged, quarter, html_path)
    generate_ranking_report_xlsx(merged, quarter, xlsx_path)
    log.info("Wrote ranking reports -> %s, %s", html_path, xlsx_path)

    return merged


def _write_empty_ranking(survivors: pd.DataFrame, quarter: str,
                          config: PipelineConfig) -> pd.DataFrame:
    out_path = (
        config.paths.intermediate_outputs_dir / f"ranked_candidates_{quarter}.parquet"
    )
    cols = _ranked_columns(list(survivors.columns), ["3mo", "12mo"])
    empty = pd.DataFrame(columns=cols)
    empty.to_parquet(out_path, index=False, engine="pyarrow")
    return empty


def _resolve_tiebreakers(tiebreakers: list[str]) -> tuple[list[str], list[bool]]:
    keys: list[str] = []
    asc: list[bool] = []
    for tb in tiebreakers:
        if tb.endswith("_desc"):
            keys.append(tb[: -len("_desc")])
            asc.append(False)
        elif tb.endswith("_asc"):
            keys.append(tb[: -len("_asc")])
            asc.append(True)
        else:
            keys.append(tb)
            asc.append(True)
    return keys, asc


# ─── Reports ─────────────────────────────────────────────────────────────────

_ARCHETYPE_PALETTE = {
    "fresh_awakening":      "#cfe9ff",
    "deep_base_breakout":   "#c2ecff",
    "early_breakout":       "#dff5e3",
    "post_crash_rebase":    "#fff1c2",
    "v_recovery":           "#d6f0d6",
    "shallow_rebase":       "#f0f5c2",
    "quiet_compression":    "#e3f5d6",
    "mature_uptrend":       "#f0e6ff",
    "stage2_pullback":      "#e6dcff",
    "extended_uptrend":     "#ffe9c2",
    "late_stage_extension": "#ffd9b0",
    "sustained_decline":    "#f3d7d7",
    "broken_trend":         "#ffe0c2",
    "parabolic_blowoff":    "#ffd6d6",
    "unclassified":         "#eeeeee",
}


def _format_num(v, fmt="{:.3f}"):
    if v is None or pd.isna(v):
        return ""
    try:
        return fmt.format(float(v))
    except Exception:
        return str(v)


def _format_int(v):
    if v is None or pd.isna(v):
        return ""
    try:
        return f"{int(v):,}"
    except Exception:
        return str(v)


def generate_ranking_report_html(df: pd.DataFrame, quarter: str,
                                  output_path: Path) -> None:
    now_iso = dt.datetime.utcnow().isoformat(timespec="seconds")
    if df.empty:
        body = (f"<!doctype html><html><body><h1>Module 4b ranking — {html.escape(quarter)}</h1>"
                f"<p>No ranked candidates.</p></body></html>")
        output_path.write_text(body, encoding="utf-8")
        return

    # Discover active horizons from column names (score_3mo, score_12mo, …).
    horizon_cols: list[str] = []
    for c in df.columns:
        if c.startswith("score_"):
            h = c[len("score_"):]
            if f"composite_{h}" in df.columns:
                horizon_cols.append(h)

    score_cols = [f"score_{h}" for h in horizon_cols]
    composite_cols = [f"composite_{h}" for h in horizon_cols]

    columns = (
        ["rank", "ticker", "name_of_issuer", "archetype"]
        + score_cols
        + ["match_confidence"]
        + composite_cols
        + ["best_horizon", "composite_best",
           "fund_count", "market_cap", "sector", "industry",
           "R_4", "R_12", "R_26", "R_52",
           "R_4_over_R_12", "R_12_over_R_26", "R_26_over_R_52",
           "young_ticker_flag"]
    )
    columns = [c for c in columns if c in df.columns]

    # Colour coding for best_horizon badge (cell-level).
    horizon_bg = {"3mo": "#e0f4ff", "12mo": "#fff3cc", "equal": "#ececec"}
    numeric_cols = set(score_cols + composite_cols) | {
        "match_confidence", "composite_best",
        "R_4", "R_12", "R_26", "R_52",
        "R_4_over_R_12", "R_12_over_R_26", "R_26_over_R_52",
    }

    rows_html = []
    for _, r in df.iterrows():
        bg = _ARCHETYPE_PALETTE.get(str(r.get("archetype")), "#fff")
        cells = []
        for c in columns:
            v = r.get(c)
            if c in ("rank", "fund_count", "market_cap"):
                cell = f"<td class='num'>{_format_int(v)}</td>"
            elif c in score_cols:
                # Scores are ints; show as "+10" / "-4" for readability.
                try:
                    iv = int(v)
                    cell = f"<td class='num'>{iv:+d}</td>" if iv != 0 else "<td class='num'>0</td>"
                except (TypeError, ValueError):
                    cell = f"<td class='num'>{_format_num(v, '{:.1f}')}</td>"
            elif c in numeric_cols:
                cell = f"<td class='num'>{_format_num(v)}</td>"
            elif c == "best_horizon":
                tag = str(v) if v is not None and not pd.isna(v) else ""
                hbg = horizon_bg.get(tag, "#fff")
                cell = f"<td style='background:{hbg}'>{html.escape(tag)}</td>"
            elif c == "young_ticker_flag":
                cell = f"<td>{'Y' if bool(v) else ''}</td>"
            else:
                cell = f"<td>{html.escape(str(v) if v is not None and not pd.isna(v) else '')}</td>"
            cells.append(cell)
        # data-* attributes feed the in-browser filter bar; raw numeric values
        # so the JS doesn't have to parse formatted strings.
        fc_val = r.get("fund_count")
        mc_val = r.get("market_cap")
        sec_val = r.get("sector")
        ind_val = r.get("industry")
        fc_attr = f" data-fund-count='{int(fc_val)}'" if pd.notna(fc_val) else ""
        mc_attr = f" data-market-cap='{int(mc_val)}'" if pd.notna(mc_val) else ""
        if pd.notna(sec_val) and str(sec_val).strip():
            sec_attr = f" data-sector='{html.escape(str(sec_val))}'"
        else:
            sec_attr = " data-sector='__missing__'"
        if pd.notna(ind_val) and str(ind_val).strip():
            ind_attr = f" data-industry='{html.escape(str(ind_val))}'"
        else:
            ind_attr = " data-industry='__missing__'"
        rows_html.append(
            f"<tr style='background:{bg}'{fc_attr}{mc_attr}{sec_attr}{ind_attr}>"
            + "".join(cells) + "</tr>"
        )

    headers_html = "".join(f"<th>{html.escape(c)}</th>" for c in columns)
    legend_items = []
    for k, v in _ARCHETYPE_PALETTE.items():
        legend_items.append(
            f"<span class='lg-item' style='background:{v}'>{html.escape(k)}</span>"
        )
    legend = " ".join(legend_items)

    # Sector dropdown: all non-empty sectors present in the ranked set,
    # plus an explicit "(no sector)" option if any row is missing one.
    sector_opts = ["<option value=''>(all sectors)</option>"]
    if "sector" in df.columns:
        uniq_sectors = sorted(
            {str(s).strip() for s in df["sector"].dropna().tolist() if str(s).strip()}
        )
        for s in uniq_sectors:
            sector_opts.append(f"<option value='{html.escape(s)}'>{html.escape(s)}</option>")
        if df["sector"].isna().any() or (df["sector"].astype(str).str.strip() == "").any():
            sector_opts.append("<option value='__missing__'>(no sector)</option>")
    sector_options_html = "".join(sector_opts)

    # Industry dropdown: same shape as sector, sourced from df["industry"].
    industry_opts = ["<option value=''>(all industries)</option>"]
    if "industry" in df.columns:
        uniq_industries = sorted(
            {str(s).strip() for s in df["industry"].dropna().tolist() if str(s).strip()}
        )
        for s in uniq_industries:
            industry_opts.append(f"<option value='{html.escape(s)}'>{html.escape(s)}</option>")
        if df["industry"].isna().any() or (df["industry"].astype(str).str.strip() == "").any():
            industry_opts.append("<option value='__missing__'>(no industry)</option>")
    industry_options_html = "".join(industry_opts)

    body = f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'>
<title>Module 4b Ranking — {html.escape(quarter)}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 1.5rem; color: #222; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .meta {{ color: #666; font-size: 0.85rem; margin-bottom: 1rem; }}
  .legend {{ margin: 0.7rem 0 0.6rem 0; font-size: 0.85rem; }}
  .lg-item {{ display: inline-block; padding: 2px 8px; margin-right: 4px;
              border-radius: 4px; border: 1px solid #ccc; }}
  .filters {{ display: flex; flex-wrap: wrap; gap: 0.8rem; align-items: center;
              padding: 0.6rem 0.8rem; margin-bottom: 0.8rem;
              background: #f7f7f7; border: 1px solid #e0e0e0; border-radius: 6px;
              font-size: 0.85rem; position: sticky; top: 0; z-index: 5; }}
  .filters label {{ display: inline-flex; align-items: center; gap: 0.3rem; }}
  .filters input {{ font: inherit; padding: 2px 6px;
                    border: 1px solid #ccc; border-radius: 3px; }}
  .filters button {{ font: inherit; padding: 2px 10px;
                     border: 1px solid #ccc; border-radius: 3px;
                     background: #fff; cursor: pointer; }}
  .filters button:hover {{ background: #eef; }}
  .filters select {{ font: inherit; padding: 2px 6px;
                     border: 1px solid #ccc; border-radius: 3px;
                     background: #fff; }}
  .filters #row-count {{ margin-left: auto; color: #555; font-variant-numeric: tabular-nums; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; }}
  th, td {{ padding: 0.25rem 0.5rem; border-bottom: 1px solid #eee; text-align: left; }}
  th {{ background: #f3f3f3; position: sticky; top: 3.4rem; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  tr:hover {{ outline: 1px solid #07a; }}
</style></head><body>
<h1>Module 4b Ranking — {html.escape(quarter)}</h1>
<div class='meta'>Generated {html.escape(now_iso)} UTC · {len(df):,} ranked candidates</div>
<div class='legend'>{legend}</div>
<div class='filters'>
  <label>Min funds: <input type='number' id='f-min-fc' min='0' step='1' style='width:60px' placeholder='0'></label>
  <label>Min cap $M: <input type='number' id='f-min-mc' min='0' step='10' style='width:90px' placeholder='0'></label>
  <label>Max cap $M: <input type='number' id='f-max-mc' min='0' step='100' style='width:90px' placeholder='∞'></label>
  <label>Sector: <select id='f-sector'>{sector_options_html}</select></label>
  <label>Industry: <select id='f-industry'>{industry_options_html}</select></label>
  <button id='f-reset' type='button'>Reset</button>
  <span id='row-count'></span>
</div>
<table>
  <thead><tr>{headers_html}</tr></thead>
  <tbody>
{"".join(rows_html)}
  </tbody>
</table>
<script>
(function() {{
  var minFc = document.getElementById('f-min-fc');
  var minMc = document.getElementById('f-min-mc');
  var maxMc = document.getElementById('f-max-mc');
  var secSel = document.getElementById('f-sector');
  var indSel = document.getElementById('f-industry');
  var reset = document.getElementById('f-reset');
  var counter = document.getElementById('row-count');
  var rows = document.querySelectorAll('tbody tr');

  function applyFilters() {{
    var minFcVal = parseFloat(minFc.value);
    var minMcVal = parseFloat(minMc.value);
    var maxMcVal = parseFloat(maxMc.value);
    if (isNaN(minFcVal)) minFcVal = 0;
    var minMcRaw = isNaN(minMcVal) ? 0 : minMcVal * 1e6;
    var maxMcRaw = isNaN(maxMcVal) ? Infinity : maxMcVal * 1e6;
    var secVal = secSel ? secSel.value : '';
    var indVal = indSel ? indSel.value : '';
    var visible = 0;
    for (var i = 0; i < rows.length; i++) {{
      var tr = rows[i];
      var fc = parseFloat(tr.dataset.fundCount);
      var mc = parseFloat(tr.dataset.marketCap);
      var rowSec = tr.dataset.sector || '__missing__';
      var rowInd = tr.dataset.industry || '__missing__';
      var fcOk = isNaN(fc) ? minFcVal === 0 : fc >= minFcVal;
      var mcOk;
      if (isNaN(mc)) {{
        mcOk = (minMcRaw === 0 && maxMcRaw === Infinity);
      }} else {{
        mcOk = mc >= minMcRaw && mc <= maxMcRaw;
      }}
      var secOk = (secVal === '') || (rowSec === secVal);
      var indOk = (indVal === '') || (rowInd === indVal);
      var ok = fcOk && mcOk && secOk && indOk;
      tr.style.display = ok ? '' : 'none';
      if (ok) visible++;
    }}
    counter.textContent = visible.toLocaleString() + ' / ' + rows.length.toLocaleString() + ' visible';
  }}

  minFc.addEventListener('input', applyFilters);
  minMc.addEventListener('input', applyFilters);
  maxMc.addEventListener('input', applyFilters);
  if (secSel) secSel.addEventListener('change', applyFilters);
  if (indSel) indSel.addEventListener('change', applyFilters);
  reset.addEventListener('click', function() {{
    minFc.value = ''; minMc.value = ''; maxMc.value = '';
    if (secSel) secSel.value = '';
    if (indSel) indSel.value = '';
    applyFilters();
  }});
  applyFilters();
}})();
</script>
</body></html>
"""
    output_path.write_text(body, encoding="utf-8")


def generate_ranking_report_xlsx(df: pd.DataFrame, quarter: str,
                                  output_path: Path) -> None:
    """Write Excel with composite conditional formatting on each composite_* col."""
    try:
        import openpyxl  # noqa: F401
        from openpyxl.formatting.rule import ColorScaleRule
        from openpyxl.utils import get_column_letter
    except ImportError:
        log.warning("openpyxl not installed; skipping XLSX export")
        return

    if df.empty:
        df.head(0).to_excel(output_path, index=False)
        return

    df.to_excel(output_path, index=False, engine="openpyxl")

    wb = openpyxl.load_workbook(output_path)
    ws = wb.active
    # Apply color scale to every composite_* column plus composite_best.
    composite_cols = [c for c in df.columns if c.startswith("composite_")]
    for col in composite_cols:
        col_idx = list(df.columns).index(col) + 1
        col_letter = get_column_letter(col_idx)
        n = len(df)
        rng = f"{col_letter}2:{col_letter}{n + 1}"
        rule = ColorScaleRule(
            start_type="min", start_color="F8696B",
            mid_type="num", mid_value=0, mid_color="FFEB84",
            end_type="max", end_color="63BE7B",
        )
        ws.conditional_formatting.add(rng, rule)
    # Auto-size first 10 columns
    for i, c in enumerate(df.columns[:12], start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(10, min(28, len(str(c)) + 4))
    wb.save(output_path)
