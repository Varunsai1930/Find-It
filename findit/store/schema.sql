-- findit/store/schema.sql — additive v2 schema for the MF holdings tracker.
--
-- Additive only: every statement uses IF NOT EXISTS so it is safe to run
-- on fresh and existing DBs. New columns on pre-existing tables
-- (mf_holdings_monthly, shareholding_quarterly) are applied via separate
-- ALTER TABLE statements at the bottom of this file and migrate() in
-- repository.py applies them idempotently (PRAGMA check + duplicate-column
-- tolerance) so existing DBs gain the columns without losing data.
--
-- Never write to ./tracker.db directly; tests use tmp_path SQLite copies.

-- ---------------------------------------------------------------- base (v1)
-- Kept identical to db.py SCHEMA for fresh-DB compatibility.

CREATE TABLE IF NOT EXISTS schemes (
    scheme_id INTEGER PRIMARY KEY AUTOINCREMENT,
    amc_name TEXT NOT NULL,
    scheme_name TEXT NOT NULL,
    UNIQUE(amc_name, scheme_name)
);

CREATE TABLE IF NOT EXISTS stocks (
    isin TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    industry TEXT,
    instrument_type TEXT
);

-- Holdings with v2 provenance columns inline for fresh DBs.
-- Existing DBs gain these via the ALTER TABLE statements below.
CREATE TABLE IF NOT EXISTS mf_holdings_monthly (
    scheme_id INTEGER NOT NULL,
    isin TEXT NOT NULL,
    report_month TEXT NOT NULL,
    quantity REAL,
    market_value_lakhs REAL,
    pct_nav REAL,
    pct_nav_raw REAL,
    pct_nav_scale TEXT,
    source_file_hash TEXT,
    ingest_run_id TEXT,
    PRIMARY KEY (scheme_id, isin, report_month),
    FOREIGN KEY (scheme_id) REFERENCES schemes(scheme_id),
    FOREIGN KEY (isin) REFERENCES stocks(isin)
);

CREATE TABLE IF NOT EXISTS mf_holding_deltas (
    scheme_id INTEGER NOT NULL,
    isin TEXT NOT NULL,
    report_month TEXT NOT NULL,
    prev_month TEXT,
    qty_change REAL,
    value_change_lakhs REAL,
    flow_lakhs REAL,
    pct_nav_change REAL,
    action TEXT NOT NULL,
    PRIMARY KEY (scheme_id, isin, report_month)
);

-- Shareholding with v2 nullable provenance columns inline for fresh DBs.
-- Existing DBs gain these via the ALTER TABLE statements below.
CREATE TABLE IF NOT EXISTS shareholding_quarterly (
    isin TEXT NOT NULL,
    quarter_end TEXT NOT NULL,
    promoter_pct REAL,
    fii_pct REAL,
    dii_pct REAL,
    public_pct REAL,
    source TEXT,
    filing_type TEXT,
    validation_status TEXT,
    source_url TEXT,
    source_sha256 TEXT,
    PRIMARY KEY (isin, quarter_end)
);

-- ---------------------------------------------------------------- v2 tables

CREATE TABLE IF NOT EXISTS instruments (
    isin TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    issuer_name TEXT,
    instrument_type TEXT,
    is_listed_equity INTEGER,
    nse_symbol TEXT,
    sector TEXT
);

CREATE TABLE IF NOT EXISTS scheme_aliases (
    alias_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_id INTEGER NOT NULL REFERENCES schemes(scheme_id),
    alias TEXT NOT NULL,
    created_at TEXT,
    UNIQUE(scheme_id, alias)
);
CREATE INDEX IF NOT EXISTS idx_scheme_aliases_scheme
    ON scheme_aliases(scheme_id);
CREATE INDEX IF NOT EXISTS idx_scheme_aliases_alias
    ON scheme_aliases(alias);

CREATE TABLE IF NOT EXISTS instrument_prices_monthly (
    isin TEXT NOT NULL,
    report_month TEXT NOT NULL,
    implied_px REAL,
    n_schemes INTEGER,
    px_cv REAL,
    PRIMARY KEY (isin, report_month)
);

CREATE TABLE IF NOT EXISTS corporate_actions (
    isin TEXT NOT NULL,
    effective_month TEXT NOT NULL,
    kind TEXT,
    ratio REAL,
    detected_by TEXT,
    confirmed INTEGER DEFAULT 0,
    PRIMARY KEY (isin, effective_month)
);

CREATE TABLE IF NOT EXISTS mf_holding_flows (
    scheme_id INTEGER NOT NULL,
    isin TEXT NOT NULL,
    report_month TEXT NOT NULL,
    prev_month TEXT,
    qty_prev REAL,
    qty_curr REAL,
    qty_delta_adjusted REAL,
    flow_lakhs REAL,
    price_effect_lakhs REAL,
    value_change_lakhs REAL,
    pct_nav_prev REAL,
    pct_nav_curr REAL,
    flow_pct_of_scheme_equity REAL,
    action TEXT NOT NULL,
    validation_status TEXT,
    pricing_method TEXT,
    PRIMARY KEY (scheme_id, isin, report_month),
    FOREIGN KEY (scheme_id) REFERENCES schemes(scheme_id)
);
CREATE INDEX IF NOT EXISTS idx_mf_holding_flows_month
    ON mf_holding_flows(report_month);

CREATE TABLE IF NOT EXISTS consensus_signals (
    isin TEXT NOT NULL,
    report_month TEXT NOT NULL,
    eligible_schemes INTEGER,
    schemes_buying INTEGER,
    schemes_selling INTEGER,
    net_flow_lakhs REAL,
    conviction_score REAL,
    buying_ratio REAL,
    PRIMARY KEY (isin, report_month)
);

CREATE TABLE IF NOT EXISTS fund_summaries (
    scheme_id INTEGER NOT NULL,
    report_month TEXT NOT NULL,
    summary_text TEXT,
    generated_by TEXT,
    generated_at TEXT,
    source_data_hash TEXT,
    model_version TEXT,
    PRIMARY KEY (scheme_id, report_month)
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    status TEXT NOT NULL,
    validation_report_json TEXT
);

CREATE TABLE IF NOT EXISTS scheme_month_status (
    scheme_id INTEGER NOT NULL,
    report_month TEXT NOT NULL,
    status TEXT NOT NULL,
    validation_report_json TEXT,
    source_data_hash TEXT,
    PRIMARY KEY (scheme_id, report_month)
);
CREATE INDEX IF NOT EXISTS idx_scheme_month_status_month
    ON scheme_month_status(report_month);

CREATE INDEX IF NOT EXISTS idx_holdings_month
    ON mf_holdings_monthly(report_month);
CREATE INDEX IF NOT EXISTS idx_holdings_scheme_month
    ON mf_holdings_monthly(scheme_id, report_month);

-- ------------------------------------------------- ALTER-friendly migrations
-- Separate statements so existing DBs can be upgraded column-by-column.
-- migrate() executes these idempotently (ignores "duplicate column" errors
-- on DBs that already have the column via the CREATE TABLEs above).

ALTER TABLE mf_holdings_monthly ADD COLUMN pct_nav_raw REAL;
ALTER TABLE mf_holdings_monthly ADD COLUMN pct_nav_scale TEXT;
ALTER TABLE mf_holdings_monthly ADD COLUMN source_file_hash TEXT;
ALTER TABLE mf_holdings_monthly ADD COLUMN ingest_run_id TEXT;

ALTER TABLE shareholding_quarterly ADD COLUMN filing_type TEXT;
ALTER TABLE shareholding_quarterly ADD COLUMN validation_status TEXT;
ALTER TABLE shareholding_quarterly ADD COLUMN source_url TEXT;
ALTER TABLE shareholding_quarterly ADD COLUMN source_sha256 TEXT;
