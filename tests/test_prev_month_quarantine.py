"""A delta compares report_month against prev_month, so a scheme whose
prev_month was itself quarantined must not vote in that report_month's
consensus -- even though report_month passed. Regression coverage for
consensus_signals._eligible_deltas (shared by compute_consensus and
voting_schemes) and the dashboard coverage counts derived from it.

Synthetic tmp-path DBs only; never touches ./tracker.db.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import consensus_signals
import db
import delta_calculator
from findit.web.app import create_app

_ISIN = "INE002A01018"


def _scheme(conn: sqlite3.Connection, sid: int, amc: str, active: int = 1) -> None:
    conn.execute(
        "INSERT INTO schemes (scheme_id, amc_name, scheme_name, scheme_key, is_active_equity) "
        "VALUES (?, ?, ?, ?, ?)",
        (sid, amc, f"Scheme {sid}", f"scheme {sid}", active),
    )


def _stock(conn: sqlite3.Connection, isin: str = _ISIN) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO stocks (isin, name, industry, instrument_type) "
        "VALUES (?, 'Reliance', 'Oil', 'equity')",
        (isin,),
    )


def _delta(conn: sqlite3.Connection, sid: int, month: str, prev: str,
          isin: str = _ISIN, action: str = "added") -> None:
    conn.execute(
        "INSERT INTO mf_holding_deltas (scheme_id, isin, report_month, prev_month, "
        "qty_change, value_change_lakhs, flow_lakhs, price_effect_lakhs, pct_nav_change, "
        "action) VALUES (?, ?, ?, ?, 1, 10, 10, 0, 0.1, ?)",
        (sid, isin, month, prev, action),
    )


def _status(conn: sqlite3.Connection, sid: int, month: str, status: str) -> None:
    conn.execute(
        "INSERT INTO scheme_month_status (scheme_id, report_month, status) VALUES (?, ?, ?)",
        (sid, month, status),
    )


# -- compute_consensus / voting_schemes --------------------------------------


def test_scheme_quarantined_in_prev_month_excluded_but_others_still_count(tmp_path):
    conn = db.get_connection(str(tmp_path / "t.db"))
    _scheme(conn, 1, "A AMC")
    _scheme(conn, 2, "B AMC")
    _stock(conn)
    _delta(conn, 1, "2026-08", "2026-07")
    _delta(conn, 2, "2026-08", "2026-07")
    _status(conn, 2, "2026-07", "quarantined")
    conn.commit()

    out = consensus_signals.compute_consensus(conn, "2026-08")
    assert len(out) == 1
    row = out.iloc[0]
    assert row["amcs_buying"] == 1
    assert row["net_amc_count"] == 1

    voters = consensus_signals.voting_schemes(conn, "2026-08")
    assert set(voters) == {1}
    conn.close()


def test_no_status_table_excludes_nothing(tmp_path):
    """Legacy DBs with no scheme_month_status table: nothing is quarantined."""
    conn = db.get_connection(str(tmp_path / "t.db"))
    _scheme(conn, 1, "A AMC")
    _stock(conn)
    _delta(conn, 1, "2026-08", "2026-07")
    conn.execute("DROP TABLE scheme_month_status")
    conn.commit()

    out = consensus_signals.compute_consensus(conn, "2026-08")
    assert len(out) == 1
    assert set(consensus_signals.voting_schemes(conn, "2026-08")) == {1}
    conn.close()


def test_report_month_quarantine_still_excluded(tmp_path):
    """Regression: quarantining report_month itself must still exclude the scheme."""
    conn = db.get_connection(str(tmp_path / "t.db"))
    _scheme(conn, 1, "A AMC")
    _stock(conn)
    _delta(conn, 1, "2026-08", "2026-07")
    _status(conn, 1, "2026-08", "quarantined")
    conn.commit()

    assert consensus_signals.compute_consensus(conn, "2026-08").empty
    assert consensus_signals.voting_schemes(conn, "2026-08") == {}
    conn.close()


# -- dashboard coverage --------------------------------------------------------


def _dashboard_db(tmp_path: Path) -> Path:
    """Two active schemes buying the same stock; scheme 2's July is quarantined."""
    dest = tmp_path / "web.db"
    conn = db.get_connection(str(dest))
    _scheme(conn, 1, "A AMC")
    _scheme(conn, 2, "B AMC")
    _stock(conn)
    for sid in (1, 2):
        for month, qty in (("2026-07", 100), ("2026-08", 150)):
            conn.execute(
                "INSERT INTO mf_holdings_monthly (scheme_id, isin, report_month, quantity, "
                "market_value_lakhs, pct_nav) VALUES (?, ?, ?, ?, ?, 10.0)",
                (sid, _ISIN, month, qty, qty * 0.1),
            )
        for month in ("2026-07", "2026-08"):
            _status(conn, sid, month, "ok")
    conn.commit()
    delta_calculator.persist_deltas(
        conn, delta_calculator.compute_deltas(conn, "2026-07", "2026-08"))
    # Quarantine discovered only after ingest: July is retroactively bad for
    # scheme 2, so its August comparison is unsafe even though August itself
    # passed cleanly.
    conn.execute(
        "UPDATE scheme_month_status SET status = 'quarantined' "
        "WHERE scheme_id = 2 AND report_month = '2026-07'"
    )
    conn.commit()
    conn.close()
    return dest


def _fact(html: str, label: str) -> str:
    fact = html[html.index(f'<dt>{label}</dt>'):]
    return fact[:fact.index("</div>")]


def test_dashboard_shows_prev_withheld_and_matches_voting_schemes(tmp_path):
    db_path = _dashboard_db(tmp_path)
    client = TestClient(create_app(str(db_path)))

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        voters = consensus_signals.voting_schemes(conn, "2026-08")
    finally:
        conn.close()
    assert set(voters) == {1}

    html = client.get("/fragments/month/2026-08").text
    schemes_fact = _fact(html, "Schemes")
    assert "<strong>1</strong> compared" in schemes_fact
    assert "1 compared against a withheld previous month" in schemes_fact
    assert "hold nothing the equity filter keeps" not in schemes_fact
