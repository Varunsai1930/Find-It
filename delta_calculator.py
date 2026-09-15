"""
delta_calculator.py — computes month-over-month holding changes per
(scheme, stock), and persists them to mf_holding_deltas.

This is deliberately pure arithmetic on numbers already in the database —
no AI involved. The AI narration layer (later) only ever narrates the
output of this file; it never computes deltas itself. See build plan §7.
"""
import sqlite3
import pandas as pd


def _fetch_month(conn: sqlite3.Connection, report_month: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT scheme_id, isin, quantity, market_value_lakhs, pct_nav "
        "FROM mf_holdings_monthly WHERE report_month = ?",
        conn, params=(report_month,),
    )


def compute_deltas(conn: sqlite3.Connection, prev_month: str, curr_month: str) -> pd.DataFrame:
    """Returns a DataFrame of deltas for every (scheme, isin) present in
    either month. Does not write to the DB — see persist_deltas for that."""
    prev = _fetch_month(conn, prev_month).set_index(["scheme_id", "isin"])
    curr = _fetch_month(conn, curr_month).set_index(["scheme_id", "isin"])

    all_keys = prev.index.union(curr.index)
    rows = []
    for key in all_keys:
        scheme_id, isin = key
        p = prev.loc[key] if key in prev.index else None
        c = curr.loc[key] if key in curr.index else None

        if p is None and c is not None:
            action = "new"
            qty_change = c["quantity"]
            value_change = c["market_value_lakhs"]
            pct_nav_change = c["pct_nav"]
        elif p is not None and c is None:
            action = "exited"
            qty_change = -p["quantity"]
            value_change = -p["market_value_lakhs"]
            pct_nav_change = -p["pct_nav"]
        else:
            qty_change = c["quantity"] - p["quantity"]
            value_change = c["market_value_lakhs"] - p["market_value_lakhs"]
            pct_nav_change = c["pct_nav"] - p["pct_nav"]
            if qty_change > 0:
                action = "added"
            elif qty_change < 0:
                action = "trimmed"
            else:
                action = "unchanged"

        rows.append({
            "scheme_id": scheme_id, "isin": isin,
            "report_month": curr_month, "prev_month": prev_month,
            "qty_change": qty_change, "value_change_lakhs": value_change,
            "pct_nav_change": pct_nav_change, "action": action,
        })

    return pd.DataFrame(rows)


def persist_deltas(conn: sqlite3.Connection, deltas: pd.DataFrame) -> int:
    cur = conn.cursor()
    for _, row in deltas.iterrows():
        cur.execute(
            """INSERT OR REPLACE INTO mf_holding_deltas
               (scheme_id, isin, report_month, prev_month, qty_change,
                value_change_lakhs, pct_nav_change, action)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(row["scheme_id"]), row["isin"], row["report_month"],
                row["prev_month"], row["qty_change"], row["value_change_lakhs"],
                row["pct_nav_change"], row["action"],
            ),
        )
    conn.commit()
    return len(deltas)
