"""
db.py — SQLite schema + loaders for the MF holdings tracker.

Kept deliberately simple for the personal-MVP phase: SQLite, no ORM,
plain SQL. Swap to Postgres later only if you actually need concurrent
writers (see the build plan, section 11).
"""
import re
import sqlite3
from pathlib import Path
import pandas as pd

SCHEMA = """
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

CREATE TABLE IF NOT EXISTS mf_holdings_monthly (
    scheme_id INTEGER NOT NULL,
    isin TEXT NOT NULL,
    report_month TEXT NOT NULL,
    quantity REAL,
    market_value_lakhs REAL,
    pct_nav REAL,
    PRIMARY KEY (scheme_id, isin, report_month),
    FOREIGN KEY (scheme_id) REFERENCES schemes(scheme_id),
    FOREIGN KEY (isin) REFERENCES stocks(isin)
);

CREATE TABLE IF NOT EXISTS mf_holding_deltas (
    scheme_id INTEGER NOT NULL,
    isin TEXT NOT NULL,
    report_month TEXT NOT NULL,       -- the "current" month of the comparison
    prev_month TEXT,
    qty_change REAL,
    value_change_lakhs REAL,
    flow_lakhs REAL,
    price_effect_lakhs REAL,
    pct_nav_change REAL,
    action TEXT NOT NULL,             -- new / added / trimmed / exited / unchanged
    PRIMARY KEY (scheme_id, isin, report_month)
);

CREATE TABLE IF NOT EXISTS shareholding_quarterly (
    isin TEXT NOT NULL,
    quarter_end TEXT NOT NULL,
    promoter_pct REAL,
    fii_pct REAL,
    dii_pct REAL,
    public_pct REAL,
    source TEXT,
    PRIMARY KEY (isin, quarter_end)
);
"""

# Additive v2 tables (never modifies SCHEMA above; applied alongside it).
V2_SCHEMA = """
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

-- Month-end exchange closes, kept apart from instrument_prices_monthly:
-- that table holds prices *implied* by fund holdings, which cannot be used
-- to check the holdings they came from. These are independent observations.
CREATE TABLE IF NOT EXISTS security_prices_monthly (
    isin TEXT NOT NULL,
    report_month TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    close_price REAL,
    series TEXT,
    symbol TEXT,
    source TEXT,
    source_url TEXT,
    fetched_at TEXT,
    PRIMARY KEY (isin, report_month)
);
"""


def classify_isin(isin: str) -> str:
    """Classifies an Indian ISIN into instrument type based on prefix and series code (chars 8-9).

    - Chars 1-2: Country ('IN')
    - Char 3: Issuer type ('E' = corporate, '0' = central govt, '9' = state govt, 'F' = mutual fund)
    - Chars 8-9: Security series ('01' = equity, '02' = preference, '07'/'08' = NCD, '14'/'16' = CP/CD)
    """
    if not isinstance(isin, str) or len(isin) < 12:
        return "other"
    isin = isin.upper().strip()
    if not isin.startswith("IN"):
        return "foreign"
    char3 = isin[2]
    series = isin[7:9]
    if char3 == "0":
        return "tbill_or_gsec"
    if char3 == "9":
        return "sgsec"
    if char3 == "F":
        return "mf_units"
    if char3 == "E":
        if series == "01":
            return "equity"
        if series == "02":
            return "preference"
        if series in ("07", "08", "09", "10", "11", "12"):
            return "ncd"
        if series in ("14", "16"):
            return "cp_or_cd"
        return "debt_other"
    return "other"


# Stopgap heuristic for consensus eligibility, pending a real AMFI
# scheme-master join (which would give stable IDs + SEBI categories).
# Case-insensitive substring match on scheme_name; anything matching is
# treated as passive/debt (0), everything else defaults to active (1).
# Defaulting to 1 is deliberate: wrongly excluding a real active fund is
# worse than wrongly including a passive one. Auditable via the backfill
# print below and the per-run passive list in run_pipeline.py.
PASSIVE_SCHEME_PATTERNS = (
    "etf", "index", "nifty", "sensex", "bse", "fof", "sdl", "gsec",
    "liquid", "overnight", "arbitrage", "debt", "bond", "money market",
    "gilt", "target maturity", "savings",
)


# Scheme identity ------------------------------------------------------------
#
# A scheme's name is the AMC's Excel *sheet name*: "SCRF", "SETFNIF50",
# "SBI  Bluechip  Fund". AMCs re-punctuate and rename these between months.
# Keying identity on the exact string forks one fund into two scheme_ids and
# manufactures a phantom full exit plus a phantom new fund in the deltas.
#
# Resolution order, most trustworthy first:
#   1. exact (amc_name, scheme_name)
#   2. a recorded alias for this AMC (operator-confirmed renames)
#   3. the normalized scheme_key (punctuation/case/spacing drift only)
# Nothing beyond character normalization is inferred: "SCRF" -> "SBI Credit
# Risk Fund" is a judgement call, so it must be recorded as an alias by a
# human via `python3 -m findit.cli.alias`, never guessed here.

_SCHEME_KEY_STRIP_RE = re.compile(r"[^a-z0-9]+")


def normalize_scheme_name(scheme_name: str) -> str:
    """Canonical scheme key: case, punctuation and spacing folded away.

    Deliberately conservative. It never drops words ("Direct"/"Regular",
    "Fund", plan names), because two distinct schemes must never collapse
    into one key. It only absorbs the formatting drift AMCs actually apply
    to the same sheet from month to month.
    """
    folded = _SCHEME_KEY_STRIP_RE.sub(" ", str(scheme_name or "").strip().lower())
    return " ".join(folded.split())


def ensure_scheme_identity_schema(conn: sqlite3.Connection) -> None:
    """Add the identity columns/indexes additively (safe to re-run)."""
    scheme_cols = [r[1] for r in conn.execute("PRAGMA table_info(schemes)").fetchall()]
    if "scheme_key" not in scheme_cols:
        conn.execute("ALTER TABLE schemes ADD COLUMN scheme_key TEXT")
    alias_cols = [r[1] for r in conn.execute("PRAGMA table_info(scheme_aliases)").fetchall()]
    if "alias_normalized" not in alias_cols:
        conn.execute("ALTER TABLE scheme_aliases ADD COLUMN alias_normalized TEXT")
    if "source" not in alias_cols:
        conn.execute("ALTER TABLE scheme_aliases ADD COLUMN source TEXT")
    # Non-unique on purpose: a genuine key collision is an ambiguity to
    # report at resolution time, not a CREATE INDEX failure on an existing DB.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_schemes_amc_key ON schemes(amc_name, scheme_key)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scheme_aliases_norm "
        "ON scheme_aliases(alias_normalized)"
    )
    conn.commit()


def backfill_scheme_identity(conn: sqlite3.Connection) -> int:
    """Fill scheme_key and register each scheme's own name as an alias.

    Returns the number of scheme rows given a key. Idempotent."""
    ensure_scheme_identity_schema(conn)
    rows = conn.execute(
        "SELECT scheme_id, scheme_name FROM schemes WHERE scheme_key IS NULL"
    ).fetchall()
    cur = conn.cursor()
    for scheme_id, scheme_name in rows:
        cur.execute(
            "UPDATE schemes SET scheme_key = ? WHERE scheme_id = ?",
            (normalize_scheme_name(scheme_name), scheme_id),
        )
    # Every scheme vouches for its own name, so a later rename can be
    # attached to the same identity without a special case.
    cur.execute(
        "INSERT OR IGNORE INTO scheme_aliases (scheme_id, alias, alias_normalized, "
        "created_at, source) SELECT scheme_id, scheme_name, scheme_key, "
        "datetime('now'), 'self' FROM schemes WHERE scheme_key IS NOT NULL"
    )
    cur.execute(
        "UPDATE scheme_aliases SET alias_normalized = ("
        "  SELECT scheme_key FROM schemes WHERE schemes.scheme_id = scheme_aliases.scheme_id"
        ") WHERE alias_normalized IS NULL AND alias IN ("
        "  SELECT scheme_name FROM schemes WHERE schemes.scheme_id = scheme_aliases.scheme_id)"
    )
    conn.commit()
    return len(rows)


class AmbiguousSchemeError(RuntimeError):
    """Two schemes in one AMC claim the same normalized identity."""


def resolve_scheme_id(
    conn: sqlite3.Connection, amc_name: str, scheme_name: str, create: bool = True
):
    """Resolve one AMC sheet name to a stable scheme_id.

    Returns the scheme_id, or None when no match exists and create=False.
    Raises AmbiguousSchemeError rather than picking one of several matches.
    """
    ensure_scheme_identity_schema(conn)
    amc = str(amc_name)
    raw = str(scheme_name)
    key = normalize_scheme_name(raw)
    cur = conn.cursor()

    row = cur.execute(
        "SELECT scheme_id FROM schemes WHERE amc_name = ? AND scheme_name = ?",
        (amc, raw),
    ).fetchone()
    if row:
        return int(row[0])

    matches = cur.execute(
        "SELECT DISTINCT s.scheme_id FROM scheme_aliases a "
        "JOIN schemes s ON s.scheme_id = a.scheme_id "
        "WHERE s.amc_name = ? AND a.alias_normalized = ?",
        (amc, key),
    ).fetchall()
    if not matches:
        matches = cur.execute(
            "SELECT scheme_id FROM schemes WHERE amc_name = ? AND scheme_key = ?",
            (amc, key),
        ).fetchall()
    if len(matches) > 1:
        raise AmbiguousSchemeError(
            f"{amc} | {raw!r} normalizes to {key!r}, which matches scheme_ids "
            f"{sorted(int(m[0]) for m in matches)}. Resolve them with "
            f"`python3 -m findit.cli.alias --merge-from <id> --merge-into <id>` "
            f"-- do not guess."
        )
    if matches:
        return int(matches[0][0])

    if not create:
        return None
    cur.execute(
        "INSERT INTO schemes (amc_name, scheme_name, scheme_key) VALUES (?, ?, ?)",
        (amc, raw, key),
    )
    scheme_id = int(cur.lastrowid)
    cur.execute(
        "INSERT OR IGNORE INTO scheme_aliases (scheme_id, alias, alias_normalized, "
        "created_at, source) VALUES (?, ?, ?, datetime('now'), 'self')",
        (scheme_id, raw, key),
    )
    conn.commit()
    return scheme_id


def record_scheme_alias(
    conn: sqlite3.Connection, scheme_id: int, alias: str, source: str = "operator"
) -> None:
    """Attach a raw sheet name to an existing scheme identity."""
    ensure_scheme_identity_schema(conn)
    row = conn.execute(
        "SELECT amc_name FROM schemes WHERE scheme_id = ?", (scheme_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"No scheme with scheme_id={scheme_id}")
    key = normalize_scheme_name(alias)
    if not key:
        raise ValueError("alias must contain at least one alphanumeric character")
    clash = conn.execute(
        "SELECT DISTINCT s.scheme_id FROM scheme_aliases a "
        "JOIN schemes s ON s.scheme_id = a.scheme_id "
        "WHERE s.amc_name = ? AND a.alias_normalized = ? AND s.scheme_id != ?",
        (row[0], key, scheme_id),
    ).fetchall()
    if clash:
        raise AmbiguousSchemeError(
            f"alias {alias!r} already resolves to scheme_id(s) "
            f"{sorted(int(c[0]) for c in clash)} within {row[0]}"
        )
    conn.execute(
        "INSERT OR IGNORE INTO scheme_aliases (scheme_id, alias, alias_normalized, "
        "created_at, source) VALUES (?, ?, ?, datetime('now'), ?)",
        (scheme_id, str(alias), key, source),
    )
    conn.commit()


def classify_scheme_active(scheme_name: str) -> int:
    """1 if the scheme looks like an active fund, 0 if passive/debt-like."""
    name = (scheme_name or "").lower()
    for pat in PASSIVE_SCHEME_PATTERNS:
        if pat in name:
            return 0
    return 1


def backfill_is_active_equity(conn: sqlite3.Connection) -> list:
    """Set is_active_equity where NULL via classify_scheme_active.

    Returns [(scheme_id, amc_name, scheme_name)] newly flagged 0, so the
    caller can print them (auditable, never a silent filter)."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(schemes)").fetchall()]
    if "is_active_equity" not in cols:
        conn.execute("ALTER TABLE schemes ADD COLUMN is_active_equity INTEGER")
    rows = conn.execute(
        "SELECT scheme_id, amc_name, scheme_name FROM schemes "
        "WHERE is_active_equity IS NULL"
    ).fetchall()
    flagged = []
    cur = conn.cursor()
    for scheme_id, amc_name, scheme_name in rows:
        active = classify_scheme_active(scheme_name or "")
        cur.execute(
            "UPDATE schemes SET is_active_equity = ? WHERE scheme_id = ?",
            (active, scheme_id),
        )
        if active == 0:
            flagged.append((scheme_id, amc_name, scheme_name))
    conn.commit()
    return flagged


def get_connection(db_path: str = "tracker.db") -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    # Ensure schema migrations are applied on existing DBs
    cols = [r[1] for r in conn.execute("PRAGMA table_info(stocks)").fetchall()]
    if "instrument_type" not in cols:
        conn.execute("ALTER TABLE stocks ADD COLUMN instrument_type TEXT")

    delta_cols = [r[1] for r in conn.execute("PRAGMA table_info(mf_holding_deltas)").fetchall()]
    if "flow_lakhs" not in delta_cols:
        conn.execute("ALTER TABLE mf_holding_deltas ADD COLUMN flow_lakhs REAL")
    if "price_effect_lakhs" not in delta_cols:
        conn.execute("ALTER TABLE mf_holding_deltas ADD COLUMN price_effect_lakhs REAL")

    # Additive v2 migrations: new tables + new nullable columns only.
    conn.executescript(V2_SCHEMA)
    for _table, _col, _ddl in (
        ("mf_holdings_monthly", "pct_nav_raw", "pct_nav_raw REAL"),
        ("mf_holdings_monthly", "pct_nav_scale", "pct_nav_scale TEXT"),
        ("mf_holdings_monthly", "source_file_hash", "source_file_hash TEXT"),
        ("mf_holdings_monthly", "ingest_run_id", "ingest_run_id TEXT"),
        ("shareholding_quarterly", "filing_type", "filing_type TEXT"),
        ("shareholding_quarterly", "validation_status", "validation_status TEXT"),
        ("shareholding_quarterly", "source_url", "source_url TEXT"),
        ("shareholding_quarterly", "source_sha256", "source_sha256 TEXT"),
    ):
        _existing = [r[1] for r in conn.execute(f"PRAGMA table_info({_table})").fetchall()]
        if _col not in _existing:
            conn.execute(f"ALTER TABLE {_table} ADD COLUMN {_ddl}")

    # Backfill stable scheme identity (scheme_key + self-aliases) so existing
    # DBs resolve renamed sheets to the scheme_id they already have.
    backfill_scheme_identity(conn)

    # Backfill is_active_equity for schemes lacking it (pattern heuristic).
    # Prints newly-flagged passive schemes so the filter stays auditable.
    newly_passive = backfill_is_active_equity(conn)
    for _sid, _amc, _scheme in newly_passive:
        print(f"  [is_active_equity=0] {_amc} | {_scheme}")

    # Backfill instrument_type if missing for any stocks
    unclassified = conn.execute(
        "SELECT isin FROM stocks WHERE instrument_type IS NULL"
    ).fetchall()
    if unclassified:
        cur = conn.cursor()
        for (isin,) in unclassified:
            cur.execute(
                "UPDATE stocks SET instrument_type = ? WHERE isin = ?",
                (classify_isin(isin), isin),
            )
        conn.commit()

    # Normalise fraction-scale pct_nav (where sum(pct_nav) <= 2.0) to percent scale (0..100)
    fraction_schemes = conn.execute("""
        SELECT scheme_id, report_month
        FROM mf_holdings_monthly
        GROUP BY scheme_id, report_month
        HAVING SUM(pct_nav) <= 2.0 AND COUNT(pct_nav_scale) = 0
    """).fetchall()
    if fraction_schemes:
        conn.execute("""
            UPDATE mf_holdings_monthly
            SET pct_nav = pct_nav * 100.0
            WHERE (scheme_id, report_month) IN (
                SELECT scheme_id, report_month
                FROM mf_holdings_monthly
                GROUP BY scheme_id, report_month
                HAVING SUM(pct_nav) <= 2.0 AND COUNT(pct_nav_scale) = 0
            )
        """)
        conn.commit()

    return conn


def load_parsed_csv(conn: sqlite3.Connection, csv_path: Path) -> int:
    """Loads one parser output CSV (one AMC, one month) into the DB.
    Upserts schemes/stocks, replaces holdings for that (scheme, month).
    Returns the number of holding rows loaded."""
    df = pd.read_csv(csv_path)
    required = {
        "isin", "instrument_name", "quantity", "market_value_lakhs",
        "pct_nav", "scheme_name", "amc_name", "report_month",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{csv_path} is missing expected columns: {missing}")

    # Normalise pct_nav to percent scale (0..100) if file stores it as fraction (0..1)
    df = df.copy()
    for _, group_indices in df.groupby(["amc_name", "scheme_name", "report_month"]).groups.items():
        nav_sum = df.loc[group_indices, "pct_nav"].sum()
        # New parser output is already normalized, including low-equity
        # snapshots with most NAV in dropped cash/TREPS rows.
        has_parser_scale = (
            "pct_nav_scale" in df
            and df.loc[group_indices, "pct_nav_scale"].notna().all()
        )
        if not has_parser_scale and nav_sum <= 2.0:
            df.loc[group_indices, "pct_nav"] = df.loc[group_indices, "pct_nav"] * 100.0

    cur = conn.cursor()
    # Identity is resolved per sheet name, so a re-punctuated or aliased
    # sheet keeps its existing scheme_id instead of forking into a new fund.
    scheme_ids = {}
    for (amc, scheme), _ in df.groupby(["amc_name", "scheme_name"]):
        scheme_ids[f"{amc}||{scheme}"] = resolve_scheme_id(conn, amc, scheme)
    conn.commit()

    # A scheme containing only cash/non-ISIN holdings carries one metadata
    # row. Register the scheme above, but never turn that row into a security.
    if "dropped_non_isin_count" in df:
        metadata_only = df["isin"].isna() | df["isin"].astype(str).str.strip().eq("")
        for _, row in df.loc[metadata_only].iterrows():
            sid = scheme_ids[f"{row['amc_name']}||{row['scheme_name']}"]
            cur.execute(
                "DELETE FROM mf_holdings_monthly WHERE scheme_id = ? AND report_month = ?",
                (sid, row["report_month"]),
            )
        df = df.loc[~metadata_only].copy()

    for _, row in df.drop_duplicates("isin").iterrows():
        cur.execute(
            "INSERT OR REPLACE INTO stocks (isin, name, industry, instrument_type) VALUES (?, ?, ?, ?)",
            (row["isin"], row["instrument_name"], row.get("industry"), classify_isin(row["isin"])),
        )

    loaded = 0
    for _, row in df.iterrows():
        key = f"{row['amc_name']}||{row['scheme_name']}"
        scheme_id = scheme_ids.get(key)
        if scheme_id is None:
            raise RuntimeError(f"scheme_id missing for {key} — this shouldn't happen")
        cur.execute(
            """INSERT OR REPLACE INTO mf_holdings_monthly
               (scheme_id, isin, report_month, quantity, market_value_lakhs, pct_nav,
                pct_nav_raw, pct_nav_scale)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scheme_id, row["isin"], row["report_month"],
                row["quantity"], row["market_value_lakhs"], row["pct_nav"],
                row.get("pct_nav_raw"), row.get("pct_nav_scale"),
            ),
        )
        loaded += 1

    conn.commit()
    return loaded


def load_shareholding_records(conn: sqlite3.Connection, records: pd.DataFrame) -> int:
    """Upserts parsed quarterly promoter/FII/DII shareholding records.

    ``records`` is intentionally a DataFrame rather than a source-specific
    parser output: BSE/NSE adapters can use the same loader without allowing
    fetch details to leak into the schema module.
    """
    required = {
        "isin", "quarter_end", "promoter_pct", "fii_pct", "dii_pct",
        "public_pct", "source",
    }
    missing = required - set(records.columns)
    if missing:
        raise ValueError(f"shareholding records are missing expected columns: {missing}")

    if records.empty:
        return 0

    # Optional provenance columns: persisted when present, NULL otherwise.
    # ixbrl_sha256 is accepted as an alias for source_sha256.
    if "ixbrl_sha256" in records.columns and "source_sha256" not in records.columns:
        records = records.rename(columns={"ixbrl_sha256": "source_sha256"})
    for col, ctype in (
        ("filing_type", "TEXT"),
        ("validation_status", "TEXT"),
        ("source_url", "TEXT"),
        ("source_sha256", "TEXT"),
    ):
        cols = [r[1] for r in conn.execute("PRAGMA table_info(shareholding_quarterly)").fetchall()]
        if col not in cols:
            conn.execute(f"ALTER TABLE shareholding_quarterly ADD COLUMN {col} {ctype}")

    cols = [
        "isin", "quarter_end", "promoter_pct", "fii_pct", "dii_pct",
        "public_pct", "source", "filing_type", "validation_status",
        "source_url", "source_sha256",
    ]
    available = [c for c in cols if c in records.columns]
    rows = records.loc[:, available].where(pd.notna(records[available]), None)

    conn.executemany(
        f"""INSERT OR REPLACE INTO shareholding_quarterly
           ({", ".join(available)})
           VALUES ({", ".join("?" for _ in available)})""",
        rows.itertuples(index=False, name=None),
    )
    conn.commit()
    return len(rows)
