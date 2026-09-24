"""Month-end price ingestion. Synthetic zips and a fake session; no network."""

import io
import zipfile
from datetime import date

import pandas as pd
import pytest

import db
from findit.ingest import prices as P


def _zip(rows) -> bytes:
    frame = pd.DataFrame(rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("BhavCopy.csv", frame.to_csv(index=False))
    return buf.getvalue()


ROW = {"TradDt": "2026-08-31", "ISIN": "INE002A01018", "TckrSymb": "RELIANCE",
       "SctySrs": "EQ", "ClsPric": 1500.0, "TtlTradgVol": 1000}


def test_parses_equity_closes_by_isin():
    out = P.parse_bhavcopy(_zip([ROW]), "2026-08", date(2026, 8, 31), "u")
    assert out.loc[0, "isin"] == "INE002A01018"
    assert out.loc[0, "close_price"] == 1500.0
    assert out.loc[0, "report_month"] == "2026-08"
    assert out.loc[0, "trade_date"] == "2026-08-31"


def test_non_equity_series_and_bad_prices_are_dropped():
    rows = [ROW,
            {**ROW, "ISIN": "IN0020200104", "SctySrs": "GB", "TckrSymb": "SGB"},
            {**ROW, "ISIN": "INE009A01021", "ClsPric": 0.0, "TckrSymb": "INFY"},
            {**ROW, "ISIN": "NOTANISIN", "TckrSymb": "X"}]
    out = P.parse_bhavcopy(_zip(rows), "2026-08", date(2026, 8, 31))
    assert list(out["isin"]) == ["INE002A01018"]


def test_duplicate_listings_keep_the_most_traded_quote():
    rows = [{**ROW, "SctySrs": "BE", "ClsPric": 1400.0, "TtlTradgVol": 5},
            {**ROW, "SctySrs": "EQ", "ClsPric": 1500.0, "TtlTradgVol": 90000}]
    out = P.parse_bhavcopy(_zip(rows), "2026-08", date(2026, 8, 31))
    assert len(out) == 1
    assert out.loc[0, "close_price"] == 1500.0


def test_a_changed_nse_format_fails_loudly():
    bad = _zip([{"TradDt": "2026-08-31", "Symbol": "RELIANCE", "Close": 1500.0}])
    with pytest.raises(P.PriceFetchError, match="missing expected column"):
        P.parse_bhavcopy(bad, "2026-08", date(2026, 8, 31))


def test_corrupt_zip_fails_loudly():
    with pytest.raises(P.PriceFetchError, match="unreadable"):
        P.parse_bhavcopy(b"not a zip", "2026-08", date(2026, 8, 31))


class _Session:
    """Serves a bhavcopy only on `available`; records what was requested."""

    def __init__(self, available):
        self.available = available
        self.requested = []

    def get(self, url, **kwargs):
        stamp = url.split("_")[-3]
        self.requested.append(stamp)

        class R:
            status_code = 200 if stamp in self.available else 404
            content = _zip([ROW]) if stamp in self.available else b""
        return R()


def test_walks_back_to_the_last_trading_day(tmp_path):
    # 2026-08-31 is a Monday; pretend it was a holiday and Friday 28th was the last day.
    session = _Session({"20260828"})
    out = P.fetch_bhavcopy("2026-08", cache_dir=tmp_path, session=session)
    assert out.loc[0, "trade_date"] == "2026-08-28"
    assert "20260830" not in session.requested, "weekends must not cost a request"


def test_never_reaches_into_a_later_month(tmp_path):
    session = _Session(set())
    with pytest.raises(P.PriceFetchError, match="no usable NSE bhavcopy"):
        P.fetch_bhavcopy("2026-08", cache_dir=tmp_path, session=session)
    assert all(s.startswith("202608") for s in session.requested)


def test_second_call_is_served_from_cache(tmp_path):
    session = _Session({"20260831"})
    P.fetch_bhavcopy("2026-08", cache_dir=tmp_path, session=session)
    count = len(session.requested)
    P.fetch_bhavcopy("2026-08", cache_dir=tmp_path, session=session)
    assert len(session.requested) == count, "a cached day must not be refetched"


@pytest.mark.parametrize("bad", ["2026", "2026-13", "not-a-month"])
def test_bad_month_is_rejected(bad):
    with pytest.raises(ValueError):
        P.month_end(bad)


def test_load_prices_upserts(tmp_path):
    conn = db.get_connection(str(tmp_path / "t.db"))
    rows = P.parse_bhavcopy(_zip([ROW]), "2026-08", date(2026, 8, 31))
    assert P.load_prices(conn, rows) == 1
    assert P.load_prices(conn, rows) == 1
    assert conn.execute("SELECT COUNT(*) FROM security_prices_monthly").fetchone()[0] == 1
    assert P.load_prices(conn, rows.iloc[0:0]) == 0
    conn.close()


# ---- legacy format and entry-day fetching --------------------------------------

def _legacy_zip(rows) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("cm31JUL2019bhav.csv", pd.DataFrame(rows).to_csv(index=False))
    return buf.getvalue()


def test_parses_the_legacy_bhavcopy_format():
    content = _legacy_zip([
        {"SYMBOL": "RELIANCE", "SERIES": "EQ", "CLOSE": 1162.4, "TOTTRDQTY": 100,
         "TIMESTAMP": "31-JUL-2019", "ISIN": "INE002A01018"},
        {"SYMBOL": "1003GS2019", "SERIES": "GS", "CLOSE": 61.97, "TOTTRDQTY": 2,
         "TIMESTAMP": "31-JUL-2019", "ISIN": "IN0020010065"},
    ])
    frame = P.parse_bhavcopy(content, None, date(2019, 7, 31))
    assert frame[["isin", "close_price", "symbol"]].values.tolist() == [
        ["INE002A01018", 1162.4, "RELIANCE"]]


@pytest.mark.parametrize("day, formats", [
    (date(2019, 7, 31), ["historical"]),
    (date(2024, 7, 3), ["BhavCopy_NSE_CM", "historical"]),
    (date(2026, 9, 11), ["BhavCopy_NSE_CM"]),
])
def test_only_formats_published_on_a_date_are_requested(day, formats):
    urls = P.bhavcopy_urls(day)
    assert len(urls) == len(formats)
    assert all(marker in url for marker, url in zip(formats, urls))


def test_entry_day_walks_forward_never_back(tmp_path):
    session = _Session({"20260914"})  # Fri 11th and weekend unavailable -> Monday
    frame = P.fetch_close_on_or_after(date(2026, 9, 11), cache_dir=tmp_path,
                                           session=session)
    assert frame["trade_date"].iloc[0] == "2026-09-14"
    assert all(stamp >= "20260911" for stamp in session.requested)


def test_load_daily_prices(tmp_path):
    conn = db.get_connection(str(tmp_path / "t.db"))
    frame = P.parse_bhavcopy(_zip([ROW]), None, date(2026, 9, 14))
    assert P.load_daily_prices(conn, frame) == 1
    assert conn.execute("SELECT trade_date FROM security_prices_daily").fetchone()[0] == "2026-09-14"
    conn.close()


def test_traded_value_is_kept_in_both_formats():
    udiff = P.parse_bhavcopy(_zip([dict(ROW, TtlTrfVal=12345.5)]), None, date(2026, 9, 14))
    assert udiff["traded_value"].tolist() == [12345.5]
    legacy = P.parse_bhavcopy(_legacy_zip([
        {"SYMBOL": "RELIANCE", "SERIES": "EQ", "CLOSE": 1162.4, "TOTTRDQTY": 100,
         "TOTTRDVAL": 116240.0, "TIMESTAMP": "31-JUL-2019", "ISIN": "INE002A01018"}]),
        None, date(2019, 7, 31))
    assert legacy["traded_value"].tolist() == [116240.0]
