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
    industry TEXT
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


def get_connection(db_path: str = "tracker.db") -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
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
            "INSERT OR REPLACE INTO stocks (isin, name, industry) VALUES (?, ?, ?)",
            (row["isin"], row["instrument_name"], row.get("industry")),
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
