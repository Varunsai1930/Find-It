"""NSE month-end closing prices, keyed by ISIN.

The UDiFF bhavcopy carries an ISIN column, so prices join to holdings
directly with no symbol-mapping step to get wrong.

These are independent observations, deliberately kept out of
``instrument_prices_monthly``: that table holds prices *implied* by fund
holdings (market_value / quantity), which cannot be used to check the
holdings they were derived from.

The parsing half is pure and offline; only ``fetch_bhavcopy`` touches the
network, and it caches each day's file so a re-run costs nothing.
"""
from __future__ import annotations

import calendar
import io
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
)
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nseindia.com/",
}
# Cash-market equity series. Excludes GB (sovereign gold bonds), debt and
# ETF-only series so a "price" always means an equity share price.
EQUITY_SERIES = ("EQ", "BE", "BZ", "SM", "ST")
CACHE_DIR = Path(".price_cache")
# A month end can fall on a long weekend; NSE has never closed for longer.
MAX_LOOKBACK_DAYS = 10


class PriceFetchError(RuntimeError):
    """A bhavcopy could not be retrieved or read as prices."""


def month_end(report_month: str) -> date:
    """Calendar month end for YYYY-MM (raises ValueError on a bad month)."""
    parts = str(report_month).split("-")
    if len(parts) != 2:
        raise ValueError(f"report_month must be YYYY-MM, got {report_month!r}")
    try:
        year, month = int(parts[0]), int(parts[1])
        return date(year, month, calendar.monthrange(year, month)[1])
    except (ValueError, calendar.IllegalMonthError) as exc:
        raise ValueError(f"report_month must be YYYY-MM, got {report_month!r}") from exc


def parse_bhavcopy(content: bytes, report_month: str, trade_date: date,
                   source_url: str = "") -> pd.DataFrame:
    """Equity closes from one bhavcopy zip (pure; no network)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise PriceFetchError("bhavcopy zip contains no CSV")
        frame = pd.read_csv(archive.open(names[0]))
    except (zipfile.BadZipFile, ValueError, pd.errors.ParserError) as exc:
        raise PriceFetchError(f"unreadable bhavcopy: {exc}") from exc

    required = {"ISIN", "TckrSymb", "SctySrs", "ClsPric", "TradDt"}
    missing = required - set(frame.columns)
    if missing:
        raise PriceFetchError(
            f"bhavcopy is missing expected column(s): {sorted(missing)} -- "
            "the NSE format changed; fix the mapping rather than guessing")

    frame = frame[frame["SctySrs"].astype(str).str.strip().isin(EQUITY_SERIES)].copy()
    frame["isin"] = frame["ISIN"].astype(str).str.strip().str.upper()
    frame["close_price"] = pd.to_numeric(frame["ClsPric"], errors="coerce")
    frame = frame[frame["isin"].str.match(r"^[A-Z]{2}[A-Z0-9]{10}$", na=False)]
    frame = frame[frame["close_price"].notna() & (frame["close_price"] > 0)]
    # One ISIN can list under several series; keep the most liquid quote.
    if "TtlTradgVol" in frame.columns:
        frame["_vol"] = pd.to_numeric(frame["TtlTradgVol"], errors="coerce").fillna(0)
        frame = frame.sort_values("_vol", ascending=False)
    frame = frame.drop_duplicates("isin", keep="first")

    return pd.DataFrame({
        "isin": frame["isin"],
        "report_month": report_month,
        "trade_date": trade_date.isoformat(),
        "close_price": frame["close_price"],
        "series": frame["SctySrs"].astype(str).str.strip(),
        "symbol": frame["TckrSymb"].astype(str).str.strip(),
        "source": "nse_bhavcopy",
        "source_url": source_url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }).reset_index(drop=True)


def fetch_bhavcopy(report_month: str, cache_dir: Path | str = CACHE_DIR,
                   session=None, timeout: int = 30) -> pd.DataFrame:
    """Closes for the last *trading* day of report_month.

    Walks back from the calendar month end until a bhavcopy exists, so
    weekends and holidays resolve to the real last trading day. Never
    reaches into a later month: a month's close must be that month's.
    """
    end = month_end(report_month)
    today = date.today()
    if end > today:
        end = today
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    http = session or requests
    attempts: list[str] = []

    for back in range(MAX_LOOKBACK_DAYS + 1):
        day = end - timedelta(days=back)
        if day.month != end.month or day.year != end.year:
            break
        stamp = day.strftime("%Y%m%d")
        url = BHAVCOPY_URL.format(yyyymmdd=stamp)
        cached = cache / f"bhavcopy_{stamp}.zip"
        content = None
        if cached.exists():
            content = cached.read_bytes()
        else:
            if day.weekday() >= 5:  # never spend a request on a weekend
                continue
            try:
                response = http.get(url, headers=BROWSER_HEADERS, timeout=timeout)
            except requests.RequestException as exc:
                attempts.append(f"{stamp}: {type(exc).__name__}")
                continue
            if response.status_code != 200 or not response.content:
                attempts.append(f"{stamp}: HTTP {response.status_code}")
                continue
            content = response.content
            cached.write_bytes(content)
        try:
            prices = parse_bhavcopy(content, report_month, day, url)
        except PriceFetchError as exc:
            attempts.append(f"{stamp}: {exc}")
            continue
        if prices.empty:
            attempts.append(f"{stamp}: no equity rows")
            continue
        return prices

    raise PriceFetchError(
        f"no usable NSE bhavcopy for {report_month} within "
        f"{MAX_LOOKBACK_DAYS} days of {end.isoformat()}; tried: "
        + ("; ".join(attempts) if attempts else "(nothing)"))


def load_prices(conn, prices: pd.DataFrame) -> int:
    """Upsert month-end closes. Returns rows written."""
    if prices is None or prices.empty:
        return 0
    columns = ["isin", "report_month", "trade_date", "close_price", "series",
               "symbol", "source", "source_url", "fetched_at"]
    missing = set(columns) - set(prices.columns)
    if missing:
        raise ValueError(f"price rows are missing expected columns: {sorted(missing)}")
    rows = prices.loc[:, columns].where(pd.notna(prices[columns]), None)
    conn.executemany(
        f"INSERT OR REPLACE INTO security_prices_monthly ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        rows.itertuples(index=False, name=None))
    conn.commit()
    return len(rows)
