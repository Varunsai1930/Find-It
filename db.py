"""
db.py — SQLite schema + loaders for the MF holdings tracker.

Kept deliberately simple for the personal-MVP phase: SQLite, no ORM,
plain SQL. Swap to Postgres later only if you actually need concurrent
writers (see the build plan, section 11).
"""
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


def get_connection(db_path: str = "tracker.db") -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    # Ensure schema migrations are applied on existing DBs
    cols = [r[1] for r in conn.execute("PRAGMA table_info(stocks)").fetchall()]
    if "instrument_type" not in cols:
        conn.execute("ALTER TABLE stocks ADD COLUMN instrument_type TEXT")

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
        HAVING SUM(pct_nav) <= 2.0
    """).fetchall()
    if fraction_schemes:
        conn.execute("""
            UPDATE mf_holdings_monthly
            SET pct_nav = pct_nav * 100.0
            WHERE (scheme_id, report_month) IN (
                SELECT scheme_id, report_month
                FROM mf_holdings_monthly
                GROUP BY scheme_id, report_month
                HAVING SUM(pct_nav) <= 2.0
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
        if nav_sum <= 2.0:
            df.loc[group_indices, "pct_nav"] = df.loc[group_indices, "pct_nav"] * 100.0

    cur = conn.cursor()
    for (amc, scheme), _ in df.groupby(["amc_name", "scheme_name"]):
        cur.execute(
            "INSERT OR IGNORE INTO schemes (amc_name, scheme_name) VALUES (?, ?)",
            (amc, scheme),
        )
    conn.commit()

    scheme_ids = dict(
        cur.execute("SELECT amc_name || '||' || scheme_name, scheme_id FROM schemes").fetchall()
    )

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
               (scheme_id, isin, report_month, quantity, market_value_lakhs, pct_nav)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                scheme_id, row["isin"], row["report_month"],
                row["quantity"], row["market_value_lakhs"], row["pct_nav"],
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

    rows = records.loc[:, [
        "isin", "quarter_end", "promoter_pct", "fii_pct", "dii_pct",
        "public_pct", "source",
    ]].where(pd.notna(records), None)

    conn.executemany(
        """INSERT OR REPLACE INTO shareholding_quarterly
           (isin, quarter_end, promoter_pct, fii_pct, dii_pct, public_pct, source)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        rows.itertuples(index=False, name=None),
    )
    conn.commit()
    return len(rows)
