"""SQLite connection + additive migrations for Hype Parser.

Single durable store at ``data/hype.db`` (D3 standalone; repo convention: SQL-only
durable state). Schema evolves via ordered additive migrations tracked by
``PRAGMA user_version`` — the same pattern as the other components.

Tables (schema v1):
  sources             — the source registry (spec 6.2 fields + history_availability,
                        the OD-2 split: can the panel era be reconstructed PIT?)
  registry_versions   — immutable frozen snapshots of the registry for PIT pinning
                        (Protocol 1: a run pins to a registry version frozen <= t).

Schema grows by additive migrations only (v1..v8); see each ``_migration_N`` for the table
it adds. v8 adds the theme-discovery jury-convergence tables.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 8


def now_iso() -> str:
    """UTC timestamp to the second (matches repo datetime discipline)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_iso() -> str:
    """UTC calendar date (for add_date stamps)."""
    return datetime.now(timezone.utc).date().isoformat()


def _migration_1(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE sources (
            source_id              TEXT PRIMARY KEY,
            name                   TEXT NOT NULL,
            edge_type              TEXT NOT NULL,
            tier                   TEXT,
            access_method          TEXT,
            diffusion_position     TEXT CHECK (diffusion_position IN
                                       ('leading','bridge','denominator')),
            signal_type            TEXT CHECK (signal_type IN
                                       ('threshold_event','volume')),
            history_availability   TEXT CHECK (history_availability IN
                                       ('queryable','forward_only')),
            jury_credibility       TEXT,
            cadence                TEXT,
            rate_limits            TEXT,
            url                    TEXT,
            scrapeability_verified INTEGER NOT NULL DEFAULT 0,
            enabled                INTEGER NOT NULL DEFAULT 1,
            notes                  TEXT,
            add_date               TEXT NOT NULL,
            created_at             TEXT NOT NULL,
            updated_at             TEXT NOT NULL
        );

        CREATE INDEX idx_sources_edge    ON sources (edge_type);
        CREATE INDEX idx_sources_diffpos ON sources (diffusion_position);

        CREATE TABLE registry_versions (
            version_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            label         TEXT NOT NULL UNIQUE,
            frozen_at     TEXT NOT NULL,
            n_sources     INTEGER NOT NULL,
            content_hash  TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            notes         TEXT
        );
        """
    )


def _migration_2(conn: sqlite3.Connection) -> None:
    # OD-2 forward archive: timestamped raw snapshots of forward_only sources, so the
    # panel era stays reconstructable for sources with no historical feed. Content is
    # stored only when its hash changes (taxonomies move ~annually) to bound growth; a
    # lightweight row is still written every run as a cadence/liveness log.
    conn.executescript(
        """
        CREATE TABLE source_snapshots (
            snapshot_id  INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id    TEXT NOT NULL,
            fetched_at   TEXT NOT NULL,
            url          TEXT,
            http_status  INTEGER,
            content_hash TEXT,
            bytes        INTEGER,
            content      TEXT,
            changed      INTEGER NOT NULL DEFAULT 0,
            error        TEXT,
            FOREIGN KEY (source_id) REFERENCES sources (source_id)
        );
        CREATE INDEX idx_snap_source ON source_snapshots (source_id, snapshot_id);
        """
    )


def _migration_3(conn: sqlite3.Connection) -> None:
    # M2 diffusion engine (Wave 1): seed sub-themes, the specialist document corpus with
    # local embeddings, per-theme membership, and the monthly mention series that feed
    # beta_spec / p_main / diffusion_ratio (Features v0.2 section 1). Zero Claude.
    conn.executescript(
        """
        CREATE TABLE themes (
            theme_id     TEXT PRIMARY KEY,
            label        TEXT NOT NULL,
            keywords     TEXT,                 -- JSON list
            descriptor   TEXT,                 -- text used to build the centroid
            arxiv_query  TEXT,
            gdelt_query  TEXT,
            wiki_article TEXT,
            embed_model  TEXT,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        );

        CREATE TABLE documents (
            doc_id          TEXT PRIMARY KEY,
            source_id       TEXT NOT NULL,
            title           TEXT,
            abstract        TEXT,
            url             TEXT,
            published_at    TEXT,
            published_month TEXT,              -- YYYY-MM
            embedding       BLOB,              -- float32 vector bytes
            embed_model     TEXT,
            embed_dim       INTEGER,
            fetched_at      TEXT NOT NULL
        );
        CREATE INDEX idx_docs_month  ON documents (published_month);
        CREATE INDEX idx_docs_source ON documents (source_id);

        CREATE TABLE theme_documents (
            theme_id  TEXT NOT NULL,
            doc_id    TEXT NOT NULL,
            cosine    REAL,
            is_member INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (theme_id, doc_id),
            FOREIGN KEY (theme_id) REFERENCES themes (theme_id),
            FOREIGN KEY (doc_id)   REFERENCES documents (doc_id)
        );

        CREATE TABLE theme_series (
            theme_id    TEXT NOT NULL,
            period      TEXT NOT NULL,         -- YYYY-MM
            n_spec      INTEGER NOT NULL DEFAULT 0,
            n_main      INTEGER NOT NULL DEFAULT 0,
            wiki_views  INTEGER NOT NULL DEFAULT 0,
            computed_at TEXT,
            PRIMARY KEY (theme_id, period),
            FOREIGN KEY (theme_id) REFERENCES themes (theme_id)
        );
        """
    )


def _migration_4(conn: sqlite3.Connection) -> None:
    # M2 Wave 3: EDGAR full-text search = a separate "filing keyword emergence" series column on
    # theme_series (kept distinct from N_spec) + theme_tickers (which companies/tickers mention the
    # theme in SEC filings — the first bridge toward constituent expansion). Hacker News, by
    # contrast, is a normal document source and needs no schema change.
    conn.executescript(
        """
        ALTER TABLE theme_series ADD COLUMN edgar_filings INTEGER NOT NULL DEFAULT 0;

        CREATE TABLE theme_tickers (
            theme_id   TEXT NOT NULL,
            ticker     TEXT NOT NULL,
            source_id  TEXT NOT NULL DEFAULT 'edgar_fts',
            n_mentions INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            PRIMARY KEY (theme_id, ticker),
            FOREIGN KEY (theme_id) REFERENCES themes (theme_id)
        );
        CREATE INDEX idx_theme_tickers ON theme_tickers (theme_id, n_mentions);
        """
    )


def _migration_5(conn: sqlite3.Connection) -> None:
    # EDGAR FTS is queried per YEAR (robust against the endpoint's intermittent 500s; ~9 requests
    # per theme instead of ~100 monthly). Yearly filing counts live in their own table, kept out of
    # the monthly theme_series. (theme_series.edgar_filings from v4 is left unused.)
    conn.executescript(
        """
        CREATE TABLE theme_edgar (
            theme_id   TEXT NOT NULL,
            year       INTEGER NOT NULL,
            n_filings  INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            PRIMARY KEY (theme_id, year),
            FOREIGN KEY (theme_id) REFERENCES themes (theme_id)
        );
        """
    )


def _migration_6(conn: sqlite3.Connection) -> None:
    # The labeled point-in-time panel (Protocol section 2) + its forward-return outcomes and the
    # PIT feature snapshot the kill-switch (Protocol section 4) regresses on. This is the gating
    # artifact: nothing downstream (Modules A/B, parameter fitting) is justified until the panel
    # exists and the kill-switch passes.
    conn.executescript(
        """
        CREATE TABLE panel (
            panel_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT,
            ticker          TEXT NOT NULL,
            t0_date         TEXT NOT NULL,          -- YYYY-MM-DD (mechanically the first week both
                                                    --   gates fire; for anchors, supplied)
            regime          TEXT,                   -- A | B | A+B
            theme           TEXT,
            mispricing_mode TEXT,                   -- value | rerating
            label           TEXT NOT NULL,          -- positive | hard_negative | easy_negative
            label_source    TEXT,                   -- pre_registered | derived
            notes           TEXT,
            created_at      TEXT,
            UNIQUE (ticker, t0_date)
        );

        CREATE TABLE panel_returns (
            panel_id      INTEGER NOT NULL,
            horizon_weeks INTEGER NOT NULL,
            start_price   REAL,
            end_price     REAL,
            fwd_return    REAL,
            max_drawup    REAL,
            max_drawdown  REAL,
            computed_at   TEXT,
            PRIMARY KEY (panel_id, horizon_weeks),
            FOREIGN KEY (panel_id) REFERENCES panel (panel_id)
        );

        CREATE TABLE panel_features (
            panel_id INTEGER NOT NULL,
            feature  TEXT NOT NULL,
            value    REAL,
            PRIMARY KEY (panel_id, feature),
            FOREIGN KEY (panel_id) REFERENCES panel (panel_id)
        );
        """
    )


def _migration_7(conn: sqlite3.Connection) -> None:
    # Stage A (D17) of the rigorous-panel escalation: point-in-time first-print fundamentals from SEC
    # EDGAR companyfacts (D16), for m_share labelling. Stores EVERY reported period per concept with
    # its `filed` date, so an as-of-t0 lookup can pick the latest period FILED <= t0 (leak-free, the
    # Protocol 1 cardinal rule) — and survivorship-free, since filings persist after a delisting.
    conn.executescript(
        """
        CREATE TABLE company_facts (
            ticker       TEXT NOT NULL,
            cik          TEXT,
            concept      TEXT NOT NULL,        -- 'revenue' | 'shares'
            unit         TEXT,                 -- 'USD' | 'shares'
            period_start TEXT,                 -- YYYY-MM-DD (None for instantaneous facts)
            period_end   TEXT NOT NULL,        -- YYYY-MM-DD
            val          REAL,
            filed        TEXT NOT NULL,        -- YYYY-MM-DD the value was first reported (PIT key)
            form         TEXT,                 -- 10-K / 10-Q / ...
            fy           INTEGER,
            fp           TEXT,
            PRIMARY KEY (ticker, concept, period_end, filed)
        );
        CREATE INDEX idx_cf_lookup ON company_facts (ticker, concept, filed);

        CREATE TABLE company_facts_log (
            ticker      TEXT PRIMARY KEY,
            cik         TEXT,
            status      TEXT,                  -- ok | partial | failed | no_cik
            error       TEXT,
            n_rows      INTEGER,
            fetched_at  TEXT
        );
        """
    )


def _migration_8(conn: sqlite3.Connection) -> None:
    # Theme discovery — jury-convergence model (D22-D26; spec/discovery_spec_v0.2.md). Additive,
    # tiny (a couple of MB target). The expert juries are ALREADY in `sources` (edge_type=awards,
    # tagged diffusion_position + jury_credibility) and ALREADY snapshotted by the OD-2 forward
    # archive — discovery PARSES those snapshots (+ a few clean APIs) into structured recognitions:
    #
    #   jury_signals      — one expert recognition (an award/finalist/designation/RFS), embedded
    #                       locally so convergence is cosine geometry, not volume.
    #   theme_convergence — which signals back a discovered theme (the convergence group membership).
    #   theme_orgs        — the constituent roster, classified listed vs private (Track A investable /
    #                       Track B watchlist + EDGAR listing-watch, D24). Listed rows carry ticker/cik.
    #   themes (+cols)    — horizon_years/confidence (the multi-year runway, D22 §5) and discovered_from
    #                       (jury_convergence vs hand-seeded), so discovered themes are distinguishable.
    conn.executescript(
        """
        CREATE TABLE jury_signals (
            signal_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id          TEXT NOT NULL,
            diffusion_position TEXT,                 -- leading | bridge | denominator (from sources)
            jury_credibility   TEXT,                 -- high | medium | low | na (from sources)
            year               INTEGER,              -- edition year of the recognition
            item_text          TEXT NOT NULL,        -- short text embedded for convergence
            entity             TEXT,                 -- the recognized company/technology/therapy
            entity_type        TEXT,                 -- company | technology | person | unknown
            url                TEXT,
            item_hash          TEXT NOT NULL,        -- dedup key within (source_id, year)
            embedding          BLOB,
            embed_model        TEXT,
            embed_dim          INTEGER,
            ingested_at        TEXT NOT NULL,
            UNIQUE (source_id, year, item_hash)
        );
        CREATE INDEX idx_jsig_source ON jury_signals (source_id, year);
        CREATE INDEX idx_jsig_pos    ON jury_signals (diffusion_position);

        CREATE TABLE theme_convergence (
            theme_id   TEXT NOT NULL,
            signal_id  INTEGER NOT NULL,
            similarity REAL,
            PRIMARY KEY (theme_id, signal_id),
            FOREIGN KEY (theme_id)  REFERENCES themes (theme_id),
            FOREIGN KEY (signal_id) REFERENCES jury_signals (signal_id)
        );

        CREATE TABLE theme_orgs (
            org_id                INTEGER PRIMARY KEY AUTOINCREMENT,
            theme_id              TEXT NOT NULL,
            org_name              TEXT NOT NULL,
            source_id             TEXT,
            listing_status        TEXT CHECK (listing_status IN ('listed','private','unknown')),
            ticker                TEXT,
            cik                   TEXT,
            country               TEXT,
            resolution_confidence REAL,
            listing_watch         INTEGER NOT NULL DEFAULT 0,   -- 1 = private, monitor EDGAR for S-1
            first_seen            TEXT,
            last_seen             TEXT,
            became_listed_at      TEXT,                          -- stamps the private->listed flip
            UNIQUE (theme_id, org_name),
            FOREIGN KEY (theme_id) REFERENCES themes (theme_id)
        );
        CREATE INDEX idx_torgs_theme  ON theme_orgs (theme_id);
        CREATE INDEX idx_torgs_status ON theme_orgs (listing_status);

        ALTER TABLE themes ADD COLUMN horizon_years      REAL;
        ALTER TABLE themes ADD COLUMN horizon_confidence TEXT;
        ALTER TABLE themes ADD COLUMN discovered_from    TEXT;
        """
    )


# Ordered list; index + 1 is the target user_version.
_MIGRATIONS = [_migration_1, _migration_2, _migration_3, _migration_4, _migration_5,
               _migration_6, _migration_7, _migration_8]


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for target, fn in enumerate(_MIGRATIONS, start=1):
        if version < target:
            fn(conn)
            conn.execute(f"PRAGMA user_version = {target}")
            conn.commit()
            log.info("applied migration -> v%d", target)


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating parent dirs + applying migrations) and return a connection."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate(conn)
    return conn


def current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]
