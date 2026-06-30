"""SQLite durable store. Parameterized SQL throughout (repo security invariant); additive migrations
keyed on ``PRAGMA user_version``.

Three tables:
* ``bars``      — daily OHLCV cache (ticker, date) PK; the durable price history.
* ``signals``   — per-(ticker, asof) signal readings, one row per signal name.
* ``predictions`` — per-(ticker, asof, run_id) weekly probability output (the deliverable rows).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from .models import Bar, Prediction, Signal

SCHEMA_VERSION = 6


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    # ------------------------------------------------------------------ schema
    def _migrate(self) -> None:
        v = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if v < 1:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bars (
                    ticker TEXT NOT NULL, date TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL, volume REAL,
                    PRIMARY KEY (ticker, date)
                );
                CREATE TABLE IF NOT EXISTS signals (
                    ticker TEXT NOT NULL, asof TEXT NOT NULL, name TEXT NOT NULL,
                    value REAL, score REAL, fired INTEGER, note TEXT,
                    PRIMARY KEY (ticker, asof, name)
                );
                CREATE TABLE IF NOT EXISTS predictions (
                    ticker TEXT NOT NULL, asof TEXT NOT NULL, run_id TEXT NOT NULL,
                    horizon_days INTEGER, p_up REAL, expected_return REAL,
                    confidence REAL, composite REAL, last_close REAL, signals_json TEXT,
                    PRIMARY KEY (ticker, asof, run_id)
                );
                CREATE INDEX IF NOT EXISTS ix_pred_run ON predictions(run_id);
                """
            )
            self.conn.execute("PRAGMA user_version=1")
            self.conn.commit()
        if v < 2:
            # v0.3 (spec D-2): multidimensional evidence, forward catalysts, tiered Claude scores,
            # and the forward prediction->realized validation ledger. Additive — v1 tables untouched.
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS evidence (
                    ticker TEXT NOT NULL, asof TEXT NOT NULL, dimension TEXT NOT NULL,
                    score REAL, features_json TEXT, note TEXT,
                    PRIMARY KEY (ticker, asof, dimension)
                );
                CREATE TABLE IF NOT EXISTS catalysts (
                    ticker TEXT NOT NULL, event_date TEXT NOT NULL, kind TEXT NOT NULL,
                    note TEXT, source TEXT,
                    PRIMARY KEY (ticker, event_date, kind)
                );
                CREATE TABLE IF NOT EXISTS scores (
                    ticker TEXT NOT NULL, asof TEXT NOT NULL, run_id TEXT NOT NULL,
                    tier TEXT, p_up REAL, p_down REAL, p_flat REAL, expected_return REAL,
                    conviction REAL, dimensions_json TEXT, memo TEXT,
                    config_hash TEXT, evidence_fingerprint TEXT, prompt_version TEXT,
                    PRIMARY KEY (ticker, asof, run_id)
                );
                CREATE TABLE IF NOT EXISTS ledger (
                    ticker TEXT NOT NULL, asof TEXT NOT NULL, run_id TEXT NOT NULL,
                    horizon_days INTEGER, p_up REAL, p_final REAL, predicted_label TEXT,
                    sigma_week REAL, realized_return REAL, realized_label TEXT, scored_at TEXT,
                    PRIMARY KEY (ticker, asof, run_id)
                );
                CREATE INDEX IF NOT EXISTS ix_evidence_asof ON evidence(asof);
                CREATE INDEX IF NOT EXISTS ix_catalysts_date ON catalysts(event_date);
                CREATE INDEX IF NOT EXISTS ix_scores_run ON scores(run_id);
                CREATE INDEX IF NOT EXISTS ix_ledger_open ON ledger(realized_label);
                """
            )
            self.conn.execute("PRAGMA user_version=2")
            self.conn.commit()
        if v < 3:
            # v0.3 M3: crash-safe Batch tracking. batch_id is persisted on SUBMIT (before polling) so a
            # killed run can --resume and re-poll instead of losing the paid batch (project memory
            # feedback_persist_during_long_api_batches).
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS batch_jobs (
                    run_id TEXT NOT NULL, tier TEXT NOT NULL, batch_id TEXT NOT NULL,
                    status TEXT, submitted_at TEXT,
                    PRIMARY KEY (run_id, tier)
                );
                CREATE INDEX IF NOT EXISTS ix_batch_status ON batch_jobs(status);
                """
            )
            self.conn.execute("PRAGMA user_version=3")
            self.conn.commit()
        if v < 4:
            # v0.3 M4: the hybrid blend writes its components onto the v1 predictions row (additive ALTERs,
            # guarded so the migration is idempotent even if a column somehow already exists).
            existing = {r[1] for r in self.conn.execute("PRAGMA table_info(predictions)")}
            for coldef in ("p_claude REAL", "p_model REAL", "disagreement REAL", "review INTEGER"):
                if coldef.split()[0] not in existing:
                    self.conn.execute(f"ALTER TABLE predictions ADD COLUMN {coldef}")
            self.conn.execute("PRAGMA user_version=4")
            self.conn.commit()
        if v < 5:
            # v0.3 M6: persisted probability calibrators (one per leg). The seed of the v2 feedback loop —
            # re-fit as the ledger fills.
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS calibration (
                    leg TEXT PRIMARY KEY, method TEXT, params_json TEXT, n INTEGER, fitted_at TEXT
                );
                """
            )
            self.conn.execute("PRAGMA user_version=5")
            self.conn.commit()
        if v < 6:
            # v0.3 M7: Stage 0a Claude discovery candidates + Stage 0b daily liquidity/vol/price gate.
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS discovery (
                    run_id TEXT NOT NULL, ticker TEXT NOT NULL, name TEXT, reason TEXT,
                    tags_json TEXT, discovered_at TEXT,
                    PRIMARY KEY (run_id, ticker)
                );
                CREATE TABLE IF NOT EXISTS universe_gate (
                    ticker TEXT NOT NULL, asof TEXT NOT NULL, status TEXT,
                    adv_usd REAL, weekly_sigma REAL, last_price REAL,
                    PRIMARY KEY (ticker, asof)
                );
                CREATE INDEX IF NOT EXISTS ix_disc_at ON discovery(discovered_at);
                CREATE INDEX IF NOT EXISTS ix_gate_status ON universe_gate(status);
                """
            )
            self.conn.execute("PRAGMA user_version=6")
            self.conn.commit()

    # ------------------------------------------------------------------ bars
    def upsert_bars(self, ticker: str, bars: Iterable[Bar]) -> int:
        rows = [(ticker, b.date, b.open, b.high, b.low, b.close, b.volume) for b in bars]
        self.conn.executemany(
            "INSERT INTO bars(ticker,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(ticker,date) DO UPDATE SET "
            "open=excluded.open,high=excluded.high,low=excluded.low,"
            "close=excluded.close,volume=excluded.volume",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_bars(self, ticker: str, limit: Optional[int] = None) -> list[Bar]:
        q = "SELECT date,open,high,low,close,volume FROM bars WHERE ticker=? ORDER BY date"
        rows = self.conn.execute(q, (ticker,)).fetchall()
        if limit:
            rows = rows[-limit:]
        return [Bar(r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]) for r in rows]

    def latest_bar_date(self, ticker: str) -> Optional[str]:
        r = self.conn.execute("SELECT MAX(date) d FROM bars WHERE ticker=?", (ticker,)).fetchone()
        return r["d"] if r else None

    # ------------------------------------------------------------------ signals / predictions
    def write_signals(self, ticker: str, asof: str, signals: list[Signal]) -> None:
        self.conn.executemany(
            "INSERT INTO signals(ticker,asof,name,value,score,fired,note) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(ticker,asof,name) DO UPDATE SET "
            "value=excluded.value,score=excluded.score,fired=excluded.fired,note=excluded.note",
            [(ticker, asof, s.name, s.value, s.score, int(s.fired), s.note) for s in signals],
        )
        self.conn.commit()

    def write_prediction(self, p: Prediction, run_id: str) -> None:
        self.conn.execute(
            "INSERT INTO predictions(ticker,asof,run_id,horizon_days,p_up,expected_return,"
            "confidence,composite,last_close,signals_json,p_claude,p_model,disagreement,review) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(ticker,asof,run_id) DO UPDATE SET "
            "horizon_days=excluded.horizon_days,p_up=excluded.p_up,"
            "expected_return=excluded.expected_return,confidence=excluded.confidence,"
            "composite=excluded.composite,last_close=excluded.last_close,signals_json=excluded.signals_json,"
            "p_claude=excluded.p_claude,p_model=excluded.p_model,"
            "disagreement=excluded.disagreement,review=excluded.review",
            (p.ticker, p.asof_date, run_id, p.horizon_days, p.p_up, p.expected_return,
             p.confidence, p.composite, p.last_close,
             json.dumps([s.__dict__ for s in p.signals]),
             p.p_claude, p.p_model, p.disagreement, int(p.review)),
        )
        self.conn.commit()

    def predictions_for_run(self, run_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM predictions WHERE run_id=? ORDER BY p_up DESC", (run_id,)
        ).fetchall()

    def latest_run_id(self) -> Optional[str]:
        r = self.conn.execute(
            "SELECT run_id FROM predictions ORDER BY asof DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return r["run_id"] if r else None

    # ------------------------------------------------------------------ evidence (Stage 2)
    def upsert_evidence(self, ticker: str, asof: str, dimension: str,
                        score: Optional[float], features: dict, note: str = "") -> None:
        self.conn.execute(
            "INSERT INTO evidence(ticker,asof,dimension,score,features_json,note) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(ticker,asof,dimension) DO UPDATE SET "
            "score=excluded.score,features_json=excluded.features_json,note=excluded.note",
            (ticker, asof, dimension, score, json.dumps(features or {}), note),
        )
        self.conn.commit()

    def get_evidence(self, ticker: str, asof: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM evidence WHERE ticker=? AND asof=? ORDER BY dimension", (ticker, asof)
        ).fetchall()

    # ------------------------------------------------------------------ catalysts (Stage 2, forward-only)
    def upsert_catalysts(self, ticker: str, rows: Iterable[tuple]) -> int:
        """rows = (event_date, kind, note, source). Scheduled/forward dates only (spec D-2/G)."""
        data = [(ticker, d, k, n, s) for (d, k, n, s) in rows]
        self.conn.executemany(
            "INSERT INTO catalysts(ticker,event_date,kind,note,source) VALUES(?,?,?,?,?) "
            "ON CONFLICT(ticker,event_date,kind) DO UPDATE SET note=excluded.note,source=excluded.source",
            data,
        )
        self.conn.commit()
        return len(data)

    def next_catalyst(self, ticker: str, on_or_after: str) -> Optional[sqlite3.Row]:
        """Soonest scheduled catalyst on/after a date — the forward-looking signal Stage 3 consumes."""
        return self.conn.execute(
            "SELECT * FROM catalysts WHERE ticker=? AND event_date>=? ORDER BY event_date LIMIT 1",
            (ticker, on_or_after),
        ).fetchone()

    # ------------------------------------------------------------------ scores (Stage 3, tiered Claude)
    def write_score(self, ticker: str, asof: str, run_id: str, **kw) -> None:
        cols = ("tier", "p_up", "p_down", "p_flat", "expected_return", "conviction",
                "dimensions_json", "memo", "config_hash", "evidence_fingerprint", "prompt_version")
        vals = [kw.get(c) for c in cols]
        self.conn.execute(
            "INSERT INTO scores(ticker,asof,run_id," + ",".join(cols) + ") "
            "VALUES(?,?,?," + ",".join("?" * len(cols)) + ") "
            "ON CONFLICT(ticker,asof,run_id) DO UPDATE SET "
            + ",".join(f"{c}=excluded.{c}" for c in cols),
            (ticker, asof, run_id, *vals),
        )
        self.conn.commit()

    def scores_for_run(self, run_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM scores WHERE run_id=? ORDER BY p_up DESC", (run_id,)
        ).fetchall()

    def latest_score(self, ticker: str, asof: str):
        """The most-recent Claude score for a ticker/asof (the `p_claude` leg for the blend)."""
        return self.conn.execute(
            "SELECT * FROM scores WHERE ticker=? AND asof=? ORDER BY rowid DESC LIMIT 1", (ticker, asof)
        ).fetchone()

    def has_fresh_score(self, ticker: str, asof: str, config_hash: str, evidence_fingerprint: str) -> bool:
        """True when this exact (ticker, asof, config, evidence) state was already scored — skip re-paying."""
        return self.conn.execute(
            "SELECT 1 FROM scores WHERE ticker=? AND asof=? AND config_hash=? AND evidence_fingerprint=? "
            "LIMIT 1", (ticker, asof, config_hash, evidence_fingerprint)
        ).fetchone() is not None

    # ------------------------------------------------------------------ ledger (validation §9.2)
    def append_ledger(self, ticker: str, asof: str, run_id: str, horizon_days: int,
                      p_up: float, p_final: float, predicted_label: Optional[str],
                      sigma_week: Optional[float]) -> None:
        """Record an open prediction (no realized outcome yet) for forward scoring."""
        self.conn.execute(
            "INSERT INTO ledger(ticker,asof,run_id,horizon_days,p_up,p_final,predicted_label,sigma_week) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(ticker,asof,run_id) DO UPDATE SET "
            "horizon_days=excluded.horizon_days,p_up=excluded.p_up,p_final=excluded.p_final,"
            "predicted_label=excluded.predicted_label,sigma_week=excluded.sigma_week",
            (ticker, asof, run_id, horizon_days, p_up, p_final, predicted_label, sigma_week),
        )
        self.conn.commit()

    def settle_ledger(self, ticker: str, asof: str, run_id: str,
                      realized_return: float, realized_label: Optional[str], scored_at: str) -> None:
        self.conn.execute(
            "UPDATE ledger SET realized_return=?,realized_label=?,scored_at=? "
            "WHERE ticker=? AND asof=? AND run_id=?",
            (realized_return, realized_label, scored_at, ticker, asof, run_id),
        )
        self.conn.commit()

    def open_ledger(self) -> list[sqlite3.Row]:
        """Predictions awaiting a realized outcome (the daily settle pass fills these in)."""
        return self.conn.execute(
            "SELECT * FROM ledger WHERE realized_label IS NULL ORDER BY asof", ()
        ).fetchall()

    def settled_ledger(self) -> list[sqlite3.Row]:
        """Predictions with a realized outcome — the forward track record (§9.2 metrics)."""
        return self.conn.execute(
            "SELECT * FROM ledger WHERE realized_label IS NOT NULL ORDER BY asof", ()
        ).fetchall()

    # ------------------------------------------------------------------ batch jobs (Stage 3, crash-safe)
    def record_batch(self, run_id: str, tier: str, batch_id: str, submitted_at: str) -> None:
        """Persist a submitted batch BEFORE polling — the crash-resume anchor."""
        self.conn.execute(
            "INSERT INTO batch_jobs(run_id,tier,batch_id,status,submitted_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(run_id,tier) DO UPDATE SET batch_id=excluded.batch_id,"
            "status=excluded.status,submitted_at=excluded.submitted_at",
            (run_id, tier, batch_id, "submitted", submitted_at),
        )
        self.conn.commit()

    def set_batch_status(self, run_id: str, tier: str, status: str) -> None:
        self.conn.execute("UPDATE batch_jobs SET status=? WHERE run_id=? AND tier=?",
                          (status, run_id, tier))
        self.conn.commit()

    def open_batches(self, run_id: str) -> list[sqlite3.Row]:
        """Submitted-but-not-done batches for a run — what `--resume` re-polls."""
        return self.conn.execute(
            "SELECT * FROM batch_jobs WHERE run_id=? AND status!='done' ORDER BY tier", (run_id,)
        ).fetchall()

    # ------------------------------------------------------------------ discovery + gate (Stage 0, M7)
    def write_discovery(self, run_id: str, candidates: list, discovered_at: str) -> int:
        rows = [(run_id, c["ticker"], c.get("name", ""), c.get("reason", ""),
                 json.dumps(c.get("tags", [])), discovered_at) for c in candidates]
        self.conn.executemany(
            "INSERT INTO discovery(run_id,ticker,name,reason,tags_json,discovered_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(run_id,ticker) DO UPDATE SET name=excluded.name,reason=excluded.reason,"
            "tags_json=excluded.tags_json,discovered_at=excluded.discovered_at", rows)
        self.conn.commit()
        return len(rows)

    def latest_discovery(self) -> list[sqlite3.Row]:
        """Candidates from the most recent discovery run (the weekly basket)."""
        r = self.conn.execute("SELECT run_id FROM discovery ORDER BY discovered_at DESC LIMIT 1").fetchone()
        if not r:
            return []
        return self.conn.execute("SELECT * FROM discovery WHERE run_id=? ORDER BY ticker",
                                 (r["run_id"],)).fetchall()

    def write_gate(self, ticker: str, asof: str, status: str, adv_usd, weekly_sigma, last_price) -> None:
        self.conn.execute(
            "INSERT INTO universe_gate(ticker,asof,status,adv_usd,weekly_sigma,last_price) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(ticker,asof) DO UPDATE SET status=excluded.status,"
            "adv_usd=excluded.adv_usd,weekly_sigma=excluded.weekly_sigma,last_price=excluded.last_price",
            (ticker, asof, status, adv_usd, weekly_sigma, last_price))
        self.conn.commit()

    def investable_tickers(self) -> list[str]:
        """Tickers whose MOST-RECENT gate row passed — the live scoring universe (Stage 0b output)."""
        return [r["ticker"] for r in self.conn.execute(
            "SELECT g.ticker FROM universe_gate g "
            "JOIN (SELECT ticker, MAX(asof) m FROM universe_gate GROUP BY ticker) x "
            "ON g.ticker=x.ticker AND g.asof=x.m WHERE g.status='pass' ORDER BY g.ticker"
        ).fetchall()]

    # ------------------------------------------------------------------ calibration (Stage 5 / M6)
    def save_calibrator(self, leg: str, calibrator, n: int, fitted_at: str, method: str = "isotonic") -> None:
        self.conn.execute(
            "INSERT INTO calibration(leg,method,params_json,n,fitted_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(leg) DO UPDATE SET method=excluded.method,params_json=excluded.params_json,"
            "n=excluded.n,fitted_at=excluded.fitted_at",
            (leg, method, json.dumps(calibrator.to_json()), n, fitted_at),
        )
        self.conn.commit()

    def load_calibrator(self, leg: str):
        """Return the persisted `Calibrator` for a leg (identity if none fitted yet)."""
        from .calibration import Calibrator
        row = self.conn.execute("SELECT params_json FROM calibration WHERE leg=?", (leg,)).fetchone()
        return Calibrator.from_json(json.loads(row["params_json"])) if row else Calibrator()

    def close(self) -> None:
        self.conn.close()
