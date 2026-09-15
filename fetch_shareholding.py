"""Fetch the two newest BSE quarterly shareholding filings for MF-held equities.

The MF database contains several debt and other non-equity ISINs.  This script
first runs the project's exact MF-holdings query, then uses NSE's public equity
master only to identify exchange-listed equity ISINs.  It never requests a BSE
shareholding filing for an ISIN outside that query result.

The BSE Reg. 31 iXBRL filing is the source because it exposes stable, tagged
SEBI-template rows.  FII is the sum of FPI Category I/II/III rows.  DII is a
conservative, documented aggregate of Mutual Funds, Banks, Insurance Companies
and Other Financial Institutions; categories such as pension funds and AIFs are
left out rather than guessed into DII.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sqlite3
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import pandas as pd
import requests

import consensus_signals
import db


NSE_EQUITY_MASTER_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
BSE_API_URL = "https://api.bseindia.com/BseIndiaAPI/api"
BSE_SITE_URL = "https://www.bseindia.com"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BSE_SITE_URL}/",
}

# BSE's current SEBI template has these labels under Public shareholders.
# This intentionally excludes AIFs, provident/pension funds and sovereign funds:
# they can be domestic, but are not part of this conservative DII definition.
DII_LABELS = {
    "mutual funds",
    "banks",
    "insurance companies",
    "other financial institutions",
}
FII_LABELS = {
    "foreign portfolio investors category i",
    "foreign portfolio investors category ii",
    "foreign portfolio investor (category - iii)",
    "foreign institutional investors",  # older filings may use this label.
}
PCT_TAG = "shareholdingasapercentageoftotalnumberofshares"


class ShareholdingFetchError(RuntimeError):
    """A source response could not be safely interpreted as shareholding data."""


@dataclass
class FetchStats:
    holdings_query_isins: int = 0
    listed_equity_isins: int = 0
    attempted: int = 0
    resolved: int = 0
    loaded_records: int = 0
    already_cached_records: int = 0
    unresolved_isins: list[str] = field(default_factory=list)
    no_filings_isins: list[str] = field(default_factory=list)
    failed_isins: list[str] = field(default_factory=list)


class PoliteSession:
    """One browser-shaped BSE session with a delay before every request."""

    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.next_request_at = 0.0
        self.session = requests.Session()
        # Do not add Origin: BSE's API returns an HTML error shell when it is set.
        self.session.headers.update(BROWSER_HEADERS)

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        remaining = self.next_request_at - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        response = self.session.get(url, timeout=45, **kwargs)
        self.next_request_at = time.monotonic() + self.delay_seconds
        response.raise_for_status()
        return response


def _normalise(text: str) -> str:
    return " ".join(str(text).replace("\xa0", " ").lower().split())


def _read_json_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_cache(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def get_mf_holding_isins(conn: sqlite3.Connection) -> pd.DataFrame:
    """Return only the existing MF holdings universe required by this phase."""
    return pd.read_sql_query(
        """SELECT DISTINCT isin,
                  (SELECT name FROM stocks WHERE stocks.isin = mf.isin) AS name
           FROM mf_holdings_monthly mf""",
        conn,
    )


def get_nse_equity_master(
    session: PoliteSession, cache_path: Path, max_age_days: int = 7,
) -> pd.DataFrame:
    """Load a cached NSE equity master, refreshing it weekly for ISIN mapping."""
    use_cache = cache_path.exists() and (
        time.time() - cache_path.stat().st_mtime < max_age_days * 86400
    )
    if use_cache:
        raw = cache_path.read_bytes()
    else:
        raw = session.get(NSE_EQUITY_MASTER_URL).content
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(raw)

    master = pd.read_csv(io.BytesIO(raw))
    master.columns = master.columns.str.strip()
    required = {"SYMBOL", "ISIN NUMBER"}
    missing = required - set(master.columns)
    if missing:
        raise ShareholdingFetchError(
            f"NSE equity master has unexpected columns; missing {sorted(missing)}"
        )
    master["ISIN NUMBER"] = master["ISIN NUMBER"].astype(str).str.strip().str.upper()
    return master.loc[:, ["SYMBOL", "ISIN NUMBER"]].drop_duplicates("ISIN NUMBER")


def resolve_bse_scrip_code(
    session: PoliteSession, isin: str, cache: dict[str, Any],
) -> str | None:
    """Resolve one known equity ISIN to a BSE scrip code and cache the result."""
    isin = isin.upper()
    if isin in cache:
        cached = cache[isin]
        return cached.get("bse_scrip_code") if isinstance(cached, dict) else None

    response = session.get(
        f"{BSE_API_URL}/PeerSmartSearch/w",
        params={"Type": "SS", "text": isin},
    )
    # The endpoint returns an HTML fragment, even with an application/json header.
    match = re.search(
        rf"liclick\('(?P<code>\d{{6}})'.*?<strong>{re.escape(isin)}</strong>",
        response.text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        cache[isin] = None
        return None

    cache[isin] = {"bse_scrip_code": match.group("code")}
    return match.group("code")


def get_bse_filings(session: PoliteSession, bse_scrip_code: str) -> list[dict[str, str]]:
    """Get at most two newest usable iXBRL Reg. 31 filings for a BSE scrip."""
    response = session.get(
        f"{BSE_API_URL}/Corp_Shareholding_ng/w",
        params={"scripcode": bse_scrip_code, "flag": "0", "indtype": ""},
    )
    try:
        payload = response.json()
    except requests.JSONDecodeError as exc:
        raise ShareholdingFetchError("BSE filing index did not return JSON") from exc

    table = payload.get("Table")
    if not isinstance(table, list):
        raise ShareholdingFetchError("BSE filing index has no Table list")

    usable: list[dict[str, str]] = []
    seen_quarters: set[str] = set()
    for filing in sorted(
        (item for item in table if isinstance(item, dict)),
        key=lambda item: item.get("EndDate") or "",
        reverse=True,
    ):
        attachment = filing.get("XBRLAttachment")
        if not filing.get("IsXBRL") or not attachment:
            continue
        try:
            quarter_end = pd.Timestamp(filing["EndDate"]).date().isoformat()
        except (KeyError, TypeError, ValueError):
            continue
        if quarter_end in seen_quarters:
            continue
        usable.append({"quarter_end": quarter_end, "attachment": attachment})
        seen_quarters.add(quarter_end)
        if len(usable) == 2:
            break
    return usable


def _row_percentage(row: Any) -> float | None:
    for tag in row.find_all():
        tag_name = _normalise(tag.attrs.get("name", "")).replace(" ", "")
        if tag_name.endswith(PCT_TAG):
            try:
                return float(tag.get_text(strip=True).replace(",", ""))
            except ValueError as exc:
                raise ShareholdingFetchError(
                    f"Invalid percentage in BSE iXBRL row: {tag.get_text(strip=True)!r}"
                ) from exc
    return None


def parse_bse_shareholding(html: str) -> dict[str, float]:
    """Extract aggregate ownership percentages from one BSE Reg. 31 iXBRL filing."""
    # BSE serves some iXBRL filings with an XML declaration and others as HTML.
    # The HTML parser handles both shapes, but BeautifulSoup warns for the XML
    # declaration despite the markup being intentionally inline-XBRL HTML.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "html.parser")
    promoter_pct: float | None = None
    public_pct: float | None = None
    dii_pct = 0.0
    fii_pct = 0.0
    seen_dii: set[str] = set()
    seen_fii: set[str] = set()

    for row in soup.find_all("tr"):
        labels = {
            _normalise(cell.get_text(" ", strip=True))
            for cell in row.find_all(["td", "th"])
            if cell.get_text(" ", strip=True)
        }
        if not labels:
            continue
        percentage = _row_percentage(row)

        if any(label.startswith("total shareholding of promoter and promoter group") for label in labels):
            # A zero-promoter company (for example a widely-held bank) may
            # leave the iXBRL percentage cell blank instead of tagging 0.00.
            promoter_pct = 0.0 if percentage is None else percentage
        if any(label.replace(" ", "").startswith("(b)=(b)(1)+(b)(2)") for label in labels):
            public_pct = 0.0 if percentage is None else percentage

        if percentage is None:
            continue

        for label in labels & DII_LABELS:
            if label not in seen_dii:
                dii_pct += percentage
                seen_dii.add(label)
        for label in labels & FII_LABELS:
            if label not in seen_fii:
                fii_pct += percentage
                seen_fii.add(label)

    if promoter_pct is None or public_pct is None:
        raise ShareholdingFetchError(
            "BSE iXBRL did not contain its aggregate promoter/public percentage rows"
        )
    return {
        "promoter_pct": promoter_pct,
        "fii_pct": round(fii_pct, 4),
        "dii_pct": round(dii_pct, 4),
        "public_pct": public_pct,
    }


def fetch_shareholding(
    conn: sqlite3.Connection,
    session: PoliteSession,
    cache_dir: Path,
    limit: int | None = None,
) -> FetchStats:
    """Fetch and load quarterly records without refetching cached ISIN/quarters."""
    stats = FetchStats()
    holdings = get_mf_holding_isins(conn)
    stats.holdings_query_isins = len(holdings)

    master = get_nse_equity_master(session, cache_dir / "nse_equity_master.csv")
    holdings["isin"] = holdings["isin"].astype(str).str.strip().str.upper()
    equities = holdings.merge(
        master, left_on="isin", right_on="ISIN NUMBER", how="inner",
    ).drop(columns="ISIN NUMBER")
    if limit is not None:
        equities = equities.head(limit)
    stats.listed_equity_isins = len(equities)

    scrip_cache_path = cache_dir / "bse_scrip_codes.json"
    scrip_cache = _read_json_cache(scrip_cache_path)
    for index, stock in equities.reset_index(drop=True).iterrows():
        isin = stock["isin"]
        stats.attempted += 1
        try:
            bse_scrip_code = resolve_bse_scrip_code(session, isin, scrip_cache)
            _write_json_cache(scrip_cache_path, scrip_cache)
            if bse_scrip_code is None:
                stats.unresolved_isins.append(isin)
                continue
            stats.resolved += 1

            filings = get_bse_filings(session, bse_scrip_code)
            if not filings:
                stats.no_filings_isins.append(isin)
                continue
            if len(filings) < 2:
                # Keep the filed quarter.  The join needs two records and will
                # naturally leave this stock without a delta until the next
                # Reg. 31 filing arrives; it must not invent a zero quarter.
                stats.no_filings_isins.append(isin)

            existing_quarters = {
                row[0] for row in conn.execute(
                    "SELECT quarter_end FROM shareholding_quarterly WHERE isin = ?",
                    (isin,),
                )
            }
            for filing in filings:
                if filing["quarter_end"] in existing_quarters:
                    stats.already_cached_records += 1
                    continue
                response = session.get(f"{BSE_SITE_URL}{filing['attachment']}")
                record = {
                    "isin": isin,
                    "quarter_end": filing["quarter_end"],
                    **parse_bse_shareholding(response.text),
                    "source": "bse_xbrl",
                }
                # The database is the cache keyed by (isin, quarter_end).
                # Persist immediately so an interrupted run never repeats a
                # successfully parsed filing on its next invocation.
                stats.loaded_records += db.load_shareholding_records(
                    conn, pd.DataFrame([record]),
                )
                existing_quarters.add(filing["quarter_end"])
        except (requests.RequestException, ShareholdingFetchError) as exc:
            stats.failed_isins.append(f"{isin}: {exc}")

        if (index + 1) % 25 == 0:
            print(f"Processed {index + 1}/{len(equities)} listed-equity ISINs")

    return stats


def _print_stats(stats: FetchStats) -> None:
    print("\n=== BSE quarterly shareholding fetch ===")
    print(f"MF-holdings query ISINs: {stats.holdings_query_isins}")
    print(f"Listed-equity ISINs tried: {stats.attempted}/{stats.listed_equity_isins}")
    print(f"Resolved to BSE scrips: {stats.resolved}")
    print(f"Quarterly records loaded: {stats.loaded_records}")
    print(f"Quarterly records already cached: {stats.already_cached_records}")
    print(f"No BSE scrip mapping: {len(stats.unresolved_isins)}")
    print(f"Fewer than two usable BSE filings: {len(stats.no_filings_isins)}")
    print(f"Fetch/parse failures: {len(stats.failed_isins)}")
    if stats.failed_isins:
        print("First failures:")
        for failure in stats.failed_isins[:10]:
            print(f"  - {failure}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="tracker.db", help="SQLite file to use")
    parser.add_argument(
        "--delay", type=float, default=0.35,
        help="Minimum seconds between NSE/BSE requests (default: 0.35)",
    )
    parser.add_argument(
        "--limit", type=int,
        help="Only process the first N eligible MF-held equities (for a smoke test)",
    )
    parser.add_argument(
        "--report-month",
        help="Also print MF + FII/DII common signals for this MF report month",
    )
    args = parser.parse_args()
    if args.delay < 0:
        parser.error("--delay must not be negative")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    db_path = Path(args.db)
    conn = db.get_connection(str(db_path))
    cache_dir = db_path.parent / ".shareholding_cache"
    stats = fetch_shareholding(
        conn, PoliteSession(args.delay), cache_dir, limit=args.limit,
    )
    _print_stats(stats)

    if args.report_month:
        joined = consensus_signals.join_shareholding_increase(
            conn, consensus_signals.compute_consensus(conn, args.report_month),
        )
        common = joined[joined["is_common_with_fii_increase"]]
        print(f"\n=== MF + FII/DII common increases, {args.report_month} ===")
        print("(none)" if common.empty else common.to_string(index=False))


if __name__ == "__main__":
    main()
