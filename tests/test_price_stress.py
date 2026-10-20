"""Adversarial price inputs and fault injection; synthetic data only."""
from datetime import date
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sqlite3

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


@pytest.mark.parametrize("daily", [False, True])
@pytest.mark.parametrize("cached", [b"truncated zip", _zip([{**ROW, "SctySrs": "GB"}])],
                         ids=["truncated", "no-equity"])
def test_unusable_cache_is_refetched_without_skipping_the_day(tmp_path, daily, cached):
    cache = tmp_path / "bhavcopy_20260831.zip"
    cache.write_bytes(cached)
    session = _Session({"20260831", "20260828", "20260901"})
    if daily:
        frame = P.fetch_close_on_or_after(date(2026, 8, 31), tmp_path, session)
    else:
        frame = P.fetch_bhavcopy("2026-08", tmp_path, session)
    assert frame["trade_date"].tolist() == ["2026-08-31"]
    assert session.requested == ["20260831"]
    assert P.parse_bhavcopy(cache.read_bytes(), None, date(2026, 8, 31))[
        "close_price"].tolist() == [1500.0]
    # A repaired cache must work offline.
    session.available.clear()
    assert P.fetch_bhavcopy("2026-08", tmp_path, session)["close_price"].tolist() == [1500.0]
    assert session.requested == ["20260831"]


def test_cache_publish_failure_preserves_existing_bytes(tmp_path, monkeypatch):
    cache = tmp_path / "bhavcopy_20260831.zip"
    cache.write_bytes(b"damaged original")
    original_replace = Path.replace

    def fail_cache_publish(self, target):
        if Path(target) == cache:
            raise OSError("injected cache publication failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_cache_publish)
    with pytest.raises(OSError, match="injected cache"):
        P.fetch_bhavcopy("2026-08", tmp_path, _Session({"20260831"}))
    assert cache.read_bytes() == b"damaged original"
    assert sorted(p.name for p in tmp_path.iterdir()) == [cache.name]


@pytest.mark.parametrize("daily", [False, True])
def test_late_batch_failure_rolls_back_all_5000_rows(tmp_path, daily):
    path = tmp_path / "prices.db"
    conn = db.get_connection(str(path))
    loader = P.load_daily_prices if daily else P.load_prices
    table = "security_prices_daily" if daily else "security_prices_monthly"
    original = P.parse_bhavcopy(_zip([ROW]), "2026-08", date(2026, 8, 31))
    try:
        loader(conn, original)
        frame = pd.concat([original] * 5000, ignore_index=True)
        frame["isin"] = [f"IN{i:010d}" for i in range(5000)]
        frame.loc[0, "isin"] = ROW["ISIN"]
        frame["close_price"] = 9000.0
        frame.loc[4999, "isin"] = None
        with pytest.raises(sqlite3.IntegrityError):
            loader(conn, frame)
        # A later successful operation must not accidentally commit the partial batch.
        conn.commit()
        with sqlite3.connect(path) as observer:
            assert observer.execute(f"SELECT isin,close_price FROM {table}").fetchall() == [
                (ROW["ISIN"], 1500.0)]
        frame.loc[4999, "isin"] = "IN0000004999"
        assert loader(conn, frame) == 5000
        assert loader(conn, frame) == 5000
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 5000
    finally:
        conn.close()


def test_simultaneous_cache_repairs_leave_a_complete_archive(tmp_path):
    cache = tmp_path / "bhavcopy_20260831.zip"
    cache.write_bytes(b"damaged")

    def fetch(_):
        return P.fetch_bhavcopy("2026-08", tmp_path, _Session({"20260831"}))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch, range(24)))
    assert all(frame["close_price"].tolist() == [1500.0] for frame in results)
    assert P.parse_bhavcopy(cache.read_bytes(), None, date(2026, 8, 31))[
        "close_price"].tolist() == [1500.0]
    assert sorted(p.name for p in tmp_path.iterdir()) == [cache.name]
