"""Adversarial price inputs and fault injection; synthetic data only."""
from datetime import date

import pandas as pd
import pytest

from findit.ingest import prices as P
from findit.store import db
from tests.test_prices import ROW, _zip
from tests.test_prices import _Session


@pytest.mark.parametrize("bad", ["inf", "Infinity", "-inf", "NaN", "1e999", "0", "-1"])
def test_nonfinite_or_nonpositive_close_cannot_replace_valid_quote(bad):
    rows = [ROW, {**ROW, "ClsPric": bad, "TtlTradgVol": 999999}]
    frame = P.parse_bhavcopy(_zip(rows), "2026-08", date(2026, 8, 31))
    assert frame["close_price"].tolist() == [1500.0]


@pytest.mark.parametrize("bad", ["inf", "-inf", "NaN", "-1"])
def test_invalid_liquidity_does_not_win_or_survive(bad):
    rows = [ROW, {**ROW, "ClsPric": 1400, "TtlTradgVol": bad}]
    frame = P.parse_bhavcopy(_zip(rows), None, date(2026, 8, 31))
    assert frame["close_price"].tolist() == [1500.0]
    frame = P.parse_bhavcopy(_zip([{**ROW, "TtlTrfVal": bad}]),
                            None, date(2026, 8, 31))
    assert pd.isna(frame.loc[0, "traded_value"])


@pytest.mark.parametrize("daily", [False, True])
@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan"), 0, -1])
def test_direct_load_rejects_invalid_close_without_changing_database(tmp_path, daily, bad):
    conn = db.get_connection(str(tmp_path / "prices.db"))
    loader = P.load_daily_prices if daily else P.load_prices
    table = "security_prices_daily" if daily else "security_prices_monthly"
    frame = P.parse_bhavcopy(_zip([ROW]), "2026-08", date(2026, 8, 31))
    try:
        loader(conn, frame)
        frame.loc[0, "close_price"] = bad
        with pytest.raises(ValueError, match="finite positive"):
            loader(conn, frame)
        assert conn.execute(f"SELECT close_price FROM {table}").fetchone()[0] == 1500.0
    finally:
        conn.close()


@pytest.mark.parametrize("month", ["2026-10", "2027-01", "9999-12"])
def test_future_month_never_relabels_todays_prices(tmp_path, monkeypatch, month):
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 15)

    monkeypatch.setattr(P, "date", FrozenDate)
    session = _Session({"20260915"})
    with pytest.raises(P.PriceFetchError, match="future"):
        P.fetch_bhavcopy(month, cache_dir=tmp_path, session=session)
    assert session.requested == []


def test_current_month_uses_only_available_days(tmp_path, monkeypatch):
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 15)

    monkeypatch.setattr(P, "date", FrozenDate)
    session = _Session({"20260915"})
    frame = P.fetch_bhavcopy("2026-09", cache_dir=tmp_path, session=session)
    assert frame["report_month"].tolist() == ["2026-09"]
    assert frame["trade_date"].tolist() == ["2026-09-15"]
