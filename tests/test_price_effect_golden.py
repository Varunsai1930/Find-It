"""Golden price-effect tests. Tmp DBs only; never writes ./tracker.db."""

from __future__ import annotations

from pathlib import Path

import pytest

import db
import delta_calculator

PREV = "2026-03"
CURR = "2026-04"

GOLDEN_SCHEME_ID = 44
GOLDEN_ISIN = "INE040A01034"
GOLDEN_PREV_QTY = 295465251
GOLDEN_PREV_VAL = 2210523.28
GOLDEN_CURR_QTY = 297027438
GOLDEN_CURR_VAL = 2105924.54


def _seed(conn, scheme_id, isin, prev=None, curr=None):
    conn.execute(
        "INSERT OR IGNORE INTO schemes (scheme_id, amc_name, scheme_name)"
        " VALUES (?, ?, ?)",
        (scheme_id, f"AMC {scheme_id}", f"Scheme {scheme_id}"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO stocks (isin, name, industry, instrument_type)"
        " VALUES (?, ?, ?, ?)",
        (isin, isin, None, "equity"),
    )
    if prev is not None:
        q, v = prev
        conn.execute(
            "INSERT OR REPLACE INTO mf_holdings_monthly"
            " (scheme_id, isin, report_month, quantity, market_value_lakhs, pct_nav)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (scheme_id, isin, PREV, float(q), float(v), 5.0),
        )
    if curr is not None:
        q, v = curr
        conn.execute(
            "INSERT OR REPLACE INTO mf_holdings_monthly"
            " (scheme_id, isin, report_month, quantity, market_value_lakhs, pct_nav)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (scheme_id, isin, CURR, float(q), float(v), 5.0),
        )
    conn.commit()


def _compute_persist_reread(conn, scheme_id, isin):
    deltas = delta_calculator.compute_deltas(conn, PREV, CURR)
    assert "price_effect_lakhs" in deltas.columns
    n = delta_calculator.persist_deltas(conn, deltas)
    assert n == len(deltas)
    row = conn.execute(
        "SELECT qty_change, value_change_lakhs, flow_lakhs,"
        " price_effect_lakhs, action"
        " FROM mf_holding_deltas"
        " WHERE scheme_id = ? AND isin = ? AND report_month = ?",
        (scheme_id, isin, CURR),
    ).fetchone()
    assert row is not None
    return deltas, row


def test_golden_hdfc_flow_vs_price(tmp_path: Path):
    conn = db.get_connection(str(tmp_path / "golden.db"))
    try:
        _seed(
            conn,
            GOLDEN_SCHEME_ID,
            GOLDEN_ISIN,
            prev=(GOLDEN_PREV_QTY, GOLDEN_PREV_VAL),
            curr=(GOLDEN_CURR_QTY, GOLDEN_CURR_VAL),
        )
        deltas, row = _compute_persist_reread(conn, GOLDEN_SCHEME_ID, GOLDEN_ISIN)
        _qty, value, flow, price, _action = row
        assert flow == pytest.approx(11075.91, abs=0.01)
        assert price == pytest.approx(-115674.65, abs=0.01)
        assert value == pytest.approx(-104598.74, abs=0.01)
        assert flow > 0
        assert value < 0
        assert flow + price == pytest.approx(value, abs=1e-6)
        # compute_deltas frame agrees with the persisted row
        grow = deltas[
            (deltas["scheme_id"] == GOLDEN_SCHEME_ID) & (deltas["isin"] == GOLDEN_ISIN)
        ].iloc[0]
        assert float(grow["flow_lakhs"]) == pytest.approx(11075.91, abs=0.01)
        assert float(grow["price_effect_lakhs"]) == pytest.approx(-115674.65, abs=0.01)
        assert float(grow["value_change_lakhs"]) == pytest.approx(-104598.74, abs=0.01)
    finally:
        conn.close()


def test_new_position_flow_equals_value(tmp_path: Path):
    conn = db.get_connection(str(tmp_path / "new.db"))
    try:
        # The fund held something else both months: a new *position* in an
        # ongoing fund, not a fund that did not exist last month.
        _seed(conn, 45, "INE009A01021", prev=(100.0, 10.0), curr=(100.0, 10.0))
        _seed(conn, 45, "INE002A01018", prev=None, curr=(10000.0, 500.0))
        _deltas, row = _compute_persist_reread(conn, 45, "INE002A01018")
        _qty, value, flow, price, _action = row
        assert flow == pytest.approx(500.0)
        assert value == pytest.approx(500.0)
        assert price == pytest.approx(0.0)
    finally:
        conn.close()


def test_exited_position_flow_minus_prev(tmp_path: Path):
    conn = db.get_connection(str(tmp_path / "exited.db"))
    try:
        _seed(conn, 46, "INE009A01021", prev=(100.0, 10.0), curr=(100.0, 10.0))
        _seed(conn, 46, "INE003A01019", prev=(10000.0, 500.0), curr=None)
        _deltas, row = _compute_persist_reread(conn, 46, "INE003A01019")
        _qty, value, flow, price, _action = row
        assert flow == pytest.approx(-500.0)
        assert value == pytest.approx(-500.0)
        assert price == pytest.approx(0.0)
    finally:
        conn.close()


def test_stored_zero_qty_exit(tmp_path: Path):
    conn = db.get_connection(str(tmp_path / "zeroqty.db"))
    try:
        _seed(conn, 47, "INE004A01020", prev=(10000.0, 500.0), curr=(0.0, 0.0))
        _deltas, row = _compute_persist_reread(conn, 47, "INE004A01020")
        _qty, value, flow, price, _action = row
        assert flow == pytest.approx(-500.0)
        assert value == pytest.approx(-500.0)
        assert price == pytest.approx(0.0)
        assert price == 0.0
    finally:
        conn.close()


def test_both_zero(tmp_path: Path):
    conn = db.get_connection(str(tmp_path / "bothzero.db"))
    try:
        _seed(conn, 48, "INE005A01021", prev=(0.0, 0.0), curr=(0.0, 0.0))
        _deltas, row = _compute_persist_reread(conn, 48, "INE005A01021")
        _qty, value, flow, price, _action = row
        assert flow == pytest.approx(0.0)
        assert price == pytest.approx(0.0)
        assert value == pytest.approx(0.0)
    finally:
        conn.close()
