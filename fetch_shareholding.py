"""Fetch BSE quarterly shareholding filings for MF-held (or all NSE) equities.

By default the two newest filings per stock are fetched, which is what the
monthly MF+FII overlap needs. ``--history N`` fetches up to N filings back
(to December 2015), which is what the quarterly signal backtest needs.
Every record carries ``published_at``, BSE's own broadcast timestamp, so a
backtest can use a filing only once it was actually public.

Two document formats are handled: the inline-XBRL HTML BSE serves for recent
quarters, and the plain XBRL instance XML it serves for 2016-2025 filings.
Both yield the same fields, including ``mf_pct`` -- mutual-fund ownership on
its own, which DII otherwise folds in with banks and insurers.

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
import hashlib
import io
import json
import re
import sqlite3
import time
import warnings
import xml.etree.ElementTree as ET
from datetime import datetime
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

# Negative scrip-code lookups are retried after 7 days; positive mappings
# are kept forever. JSON cache stays backward-compatible: old `None`
# negatives and old {"bse_scrip_code": ...} positives still read correctly.
NEGATIVE_CACHE_TTL_SECONDS = 7 * 86400

# Quarter-end month-days that count as quarterly Reg. 31 filings; anything
# else is an interim filing. Both are kept — never filtered, never invented.
QUARTERLY_MONTH_DAYS = {"03-31", "06-30", "09-30", "12-31"}


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


def _normalize_fii_label(label: str) -> str:
    """Normalize an FII row label: strip parens/hyphens, collapse spaces."""
    s = str(label).replace("\xa0", " ").lower()
    for ch in ("(", ")", "-", "–", "—", "/", ","):
        s = s.replace(ch, " ")
    return " ".join(s.split())


_FPI_CATEGORY_RE = re.compile(
    r"foreign\s+portfolio\s+investor[s]?\b(?:\s*category\s*(i{1,3}|1|2|3))?"
)
_FII_LEGACY_RE = re.compile(r"foreign\s+institutional\s+investor[s]?\b")


def _fii_category(normalized_label: str) -> str | None:
    """Map a normalized FII label to i/ii/iii/aggregate/legacy, else None."""
    if _FII_LEGACY_RE.search(normalized_label):
        return "legacy"
    m = _FPI_CATEGORY_RE.search(normalized_label)
    if not m:
        return None
    cat = (m.group(1) or "").strip().lower()
    if not cat:
        return "aggregate"
    if cat in ("1", "i"):
        return "i"
    if cat in ("2", "ii"):
        return "ii"
    if cat in ("3", "iii"):
        return "iii"
    return cat


def classify_filing_type(quarter_end: str) -> str:
    """Classify a filing quarter_end as quarterly vs interim.

    Quarterly month-days are 03-31/06-30/09-30/12-31; anything else is
    interim. Both kinds are kept by the fetcher — this only labels them.
    """
    md: str | None = None
    try:
        md = pd.Timestamp(quarter_end).strftime("%m-%d")
    except Exception:
        try:
            md = str(quarter_end).strip()[5:10]
        except Exception:
            md = None
    if md in QUARTERLY_MONTH_DAYS:
        return "quarterly"
    return "interim"


def _persist_ixbrl_attachment(cache_dir: Path, content: bytes) -> str:
    """Persist raw iXBRL bytes under attachments/<sha256>.ixbrl; return sha256."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    sha = hashlib.sha256(bytes(content)).hexdigest()
    dest = Path(cache_dir) / "attachments" / f"{sha}.ixbrl"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(bytes(content))
    return sha


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
    """Resolve one known equity ISIN to a BSE scrip code and cache the result.

    Positive entries are kept forever. Negative entries carry
    cached_at+reason and are retried after 7 days. Old cache files
    (None negatives, plain positives) remain readable.
    """
    isin = isin.upper()
    if isin in cache:
        cached = cache[isin]
        if isinstance(cached, dict):
            code = cached.get("bse_scrip_code")
            if code:
                return code
            # Negative dict: honour the 7-day TTL, else fall through to retry.
            cached_at = cached.get("cached_at", cached.get("timestamp"))
            if cached_at is not None:
                try:
                    age = time.time() - float(cached_at)
                except (TypeError, ValueError):
                    age = float("inf")
                if age < NEGATIVE_CACHE_TTL_SECONDS:
                    return None
            # Expired (or timestamp-less negative dict) -> retry below.
        elif cached is None:
            # Backward-compatible old negative with no timestamp -> retry.
            pass
        else:
            # Unexpected shape -> treat as a miss and retry.
            pass

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
        cache[isin] = {
            "bse_scrip_code": None,
            "cached_at": time.time(),
            "reason": "no_match",
        }
        return None

    cache[isin] = {"bse_scrip_code": match.group("code")}
    return match.group("code")


def filing_published_at(filing: dict) -> str | None:
    """BSE's broadcast timestamp for a filing as ISO text, or None.

    ``D`` is ISO already; ``broadcastTime`` ("Jul 16 2026  7:24PM") is the
    fallback. Unparseable or absent means unknown -- never guessed here.
    """
    raw = filing.get("D")
    if raw:
        try:
            return datetime.fromisoformat(str(raw).strip()).isoformat(timespec="minutes")
        except ValueError:
            pass
    raw = filing.get("broadcastTime")
    if raw:
        try:
            return datetime.strptime(" ".join(str(raw).split()), "%b %d %Y %I:%M%p").isoformat(
                timespec="minutes")
        except ValueError:
            pass
    return None


def get_bse_filings(
    session: PoliteSession, bse_scrip_code: str, max_filings: int | None = 2,
) -> list[dict[str, str]]:
    """Get the newest usable XBRL Reg. 31 filings for a BSE scrip.

    Each filing carries quarter_end, attachment, filing_type (quarterly vs
    interim) and published_at. Both types are kept in recency order.
    ``max_filings=None`` returns every usable filing.
    """
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
        # Some early entries point at a bare directory ("/XBRL1/").
        if not filing.get("IsXBRL") or not attachment or str(attachment).endswith("/"):
            continue
        try:
            quarter_end = pd.Timestamp(filing["EndDate"]).date().isoformat()
        except (KeyError, TypeError, ValueError):
            continue
        if quarter_end in seen_quarters:
            continue
        usable.append(
            {
                "quarter_end": quarter_end,
                "attachment": attachment,
                "filing_type": classify_filing_type(quarter_end),
                "published_at": filing_published_at(filing),
            }
        )
        seen_quarters.add(quarter_end)
        if max_filings is not None and len(usable) >= max_filings:
            break
    return usable


def _row_percentage(row: Any) -> float | None:
    """Pin the intended denominator for one iXBRL row.

    Prefers tags containing 'totalnumberofshares' when present, otherwise
    uses the remaining shareholding-as-percentage tags. Asserts at most one
    distinct value among the chosen tags, else raises (ambiguous filing).
    """
    candidates: list[tuple[str, float]] = []
    for tag in row.find_all():
        tag_name = _normalise(tag.attrs.get("name", "")).replace(" ", "")
        if "shareholdingasapercentage" not in tag_name:
            continue
        text = tag.get_text(strip=True).replace(",", "")
        if text == "":
            continue
        try:
            value = float(text)
        except ValueError as exc:
            raise ShareholdingFetchError(
                f"Invalid percentage in BSE iXBRL row: {tag.get_text(strip=True)!r}"
            ) from exc
        candidates.append((tag_name, value))
    if not candidates:
        return None
    preferred = [(n, v) for n, v in candidates if "totalnumberofshares" in n]
    pool = preferred if preferred else candidates
    distinct = sorted({v for _, v in pool})
    if len(distinct) > 1:
        raise ShareholdingFetchError(
            f"Ambiguous percentage tags in BSE iXBRL row: {pool!r}"
        )
    return distinct[0]


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
    mf_pct: float | None = None
    seen_dii: set[str] = set()
    # FII categories tracked separately so an aggregate row plus its
    # Category I/II/III sub-rows are never double-counted.
    fii_by_category: dict[str, float] = {}

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
                if label == "mutual funds":
                    mf_pct = percentage
        row_cats: set[str] = set()
        for label in labels:
            cat = _fii_category(_normalize_fii_label(label))
            if cat is not None:
                row_cats.add(cat)
        for cat in row_cats:
            # Track seen categories: repeated rows for the same category
            # never double-count.
            if cat not in fii_by_category:
                fii_by_category[cat] = percentage

    sub_cats = [c for c in ("i", "ii", "iii") if c in fii_by_category]
    if sub_cats:
        # Sub-rows present: ignore any aggregate row to avoid double count.
        fii_pct = round(sum(fii_by_category[c] for c in sub_cats), 4)
    elif "aggregate" in fii_by_category:
        fii_pct = round(fii_by_category["aggregate"], 4)
    elif "legacy" in fii_by_category:
        fii_pct = round(fii_by_category["legacy"], 4)
    else:
        fii_pct = 0.0

    if promoter_pct is None or public_pct is None:
        raise ShareholdingFetchError(
            "BSE iXBRL did not contain its aggregate promoter/public percentage rows"
        )
    return {
        "promoter_pct": promoter_pct,
        "fii_pct": round(fii_pct, 4),
        "dii_pct": round(dii_pct, 4),
        "public_pct": public_pct,
        "mf_pct": None if mf_pct is None else round(mf_pct, 4),
    }


# Category contexts in BSE's XBRL instance documents (2016-2025). A context
# id is the category name plus "I" (2016) or "_ContextI" (2020+) for the
# quarter-end instant; numbered contexts ("..._Context15") are named holders.
_XBRL_CATEGORY_CONTEXT_RE = re.compile(r"(.+?)(?:_Context)?I")
_XBRL_PCT_ELEMENT = "ShareholdingAsAPercentageOfTotalNumberOfShares"
_XBRL_PROMOTER = "ShareholdingOfPromoterAndPromoterGroup"
_XBRL_PUBLIC = "PublicShareholding"
_XBRL_TOTAL = "ShareholdingPattern"
_XBRL_MF = "MutualFundsOrUti"
# The taxonomy spells it "Catergory"; accept the correct spelling too.
_XBRL_FPI_CATEGORY_RE = re.compile(
    r"InstitutionsForeignPortfolioInvestorCate?r?gory(One|Two|Three)")
_XBRL_FPI_AGGREGATE = "InstitutionsForeignPortfolioInvestor"
_XBRL_FII_LEGACY_RE = re.compile(r"ForeignInstitutionalInvestors?")
# Same conservative DII definition as DII_LABELS for the HTML format.
_XBRL_DII = (_XBRL_MF, "Banks", "FinancialInstitutionOrBanks",
             "InsuranceCompanies", "OtherFinancialInstitutions")


def parse_bse_shareholding_xbrl(document: str | bytes) -> dict[str, float]:
    """Extract ownership percentages from one BSE Reg. 31 XBRL instance document."""
    if isinstance(document, str):
        document = document.encode("utf-8")
    try:
        root = ET.fromstring(document.lstrip(b"\xef\xbb\xbf \r\n\t"))
    except ET.ParseError as exc:
        raise ShareholdingFetchError(f"BSE XBRL is not well-formed XML: {exc}") from exc

    values: dict[str, float] = {}
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != _XBRL_PCT_ELEMENT:
            continue
        match = _XBRL_CATEGORY_CONTEXT_RE.fullmatch(element.get("contextRef") or "")
        text = (element.text or "").strip().replace(",", "")
        if not match or not text:
            continue
        try:
            value = float(text)
        except ValueError as exc:
            raise ShareholdingFetchError(
                f"Invalid percentage in BSE XBRL: {element.get('contextRef')}={text!r}"
            ) from exc
        name = match.group(1)
        if name in values and values[name] != value:
            raise ShareholdingFetchError(
                f"Conflicting percentages for {name} in BSE XBRL: {values[name]} vs {value}")
        values[name] = value

    # A company with no promoter group (ITC, most banks) files no promoter
    # line at all. Read that as 0 only when the filing itself says the public
    # holds everything (the total line, or 100% where 2016 filings omit it);
    # anything else stays a loud failure.
    if (_XBRL_PROMOTER not in values and _XBRL_PUBLIC in values
            and abs(values[_XBRL_PUBLIC] - values.get(_XBRL_TOTAL, 100.0)) <= 0.01):
        values[_XBRL_PROMOTER] = 0.0
    if _XBRL_PROMOTER not in values or _XBRL_PUBLIC not in values:
        raise ShareholdingFetchError(
            "BSE XBRL did not contain its aggregate promoter/public percentage contexts"
        )
    fpi_categories = [v for k, v in values.items() if _XBRL_FPI_CATEGORY_RE.fullmatch(k)]
    if fpi_categories:
        fii_pct = sum(fpi_categories)
    elif _XBRL_FPI_AGGREGATE in values:
        fii_pct = values[_XBRL_FPI_AGGREGATE]
    else:
        legacy = [v for k, v in values.items() if _XBRL_FII_LEGACY_RE.search(k)]
        fii_pct = legacy[0] if legacy else 0.0
    dii_pct = sum(values[k] for k in _XBRL_DII if k in values)
    out = {
        "promoter_pct": values[_XBRL_PROMOTER],
        "fii_pct": round(fii_pct, 4),
        "dii_pct": round(dii_pct, 4),
        "public_pct": values[_XBRL_PUBLIC],
        "mf_pct": round(values[_XBRL_MF], 4) if _XBRL_MF in values else None,
    }
    for key, value in out.items():
        if value is not None and not 0.0 <= value <= 100.0:
            raise ShareholdingFetchError(f"BSE XBRL {key}={value} is outside 0-100")
    return out


def parse_shareholding_document(document: str | bytes) -> dict[str, float]:
    """Parse either BSE format: XBRL instance XML or inline-XBRL HTML."""
    text = document.decode("utf-8", errors="replace") if isinstance(document, bytes) else document
    head = text[:4000]
    if "xbrli:xbrl" in head and "<tr" not in text:
        return parse_bse_shareholding_xbrl(document)
    return parse_bse_shareholding(text)


def fetch_shareholding(
    conn: sqlite3.Connection,
    session: PoliteSession,
    cache_dir: Path,
    limit: int | None = None,
    only_missing: bool = False,
    history: int | None = 2,
    universe: str = "held",
) -> FetchStats:
    """Fetch and load quarterly records without refetching cached ISIN/quarters.

    ``history`` is the number of newest filings per stock (None = all).
    ``universe`` is "held" (ISINs any tracked scheme holds) or "nse" (every
    NSE-listed equity -- a broader, less selection-biased research universe).
    A stored filing is re-downloaded only when it predates ``mf_pct``; a
    missing ``published_at`` is filled from the filing index alone.
    """
    if universe not in ("held", "nse"):
        raise ValueError(f"universe must be 'held' or 'nse', got {universe!r}")
    present = {r[1] for r in conn.execute("PRAGMA table_info(shareholding_quarterly)")}
    for column, ddl in (("mf_pct", "REAL"), ("published_at", "TEXT")):
        if column not in present:
            conn.execute(f"ALTER TABLE shareholding_quarterly ADD COLUMN {column} {ddl}")
    stats = FetchStats()
    master = get_nse_equity_master(session, cache_dir / "nse_equity_master.csv")
    if universe == "nse":
        equities = master.rename(columns={"ISIN NUMBER": "isin"}).assign(name=None)
        stats.holdings_query_isins = len(equities)
    else:
        holdings = get_mf_holding_isins(conn)
        stats.holdings_query_isins = len(holdings)
        holdings["isin"] = holdings["isin"].astype(str).str.strip().str.upper()
        equities = holdings.merge(
            master, left_on="isin", right_on="ISIN NUMBER", how="inner",
        ).drop(columns="ISIN NUMBER")
    if only_missing:
        wanted = history if history is not None else 1
        complete_isins = {
            row[0] for row in conn.execute(
                """SELECT isin
                   FROM shareholding_quarterly
                   GROUP BY isin
                   HAVING COUNT(*) >= ?
                      AND SUM(mf_pct IS NULL) = 0
                      AND SUM(published_at IS NULL) = 0""",
                (wanted,),
            )
        }
        equities = equities[~equities["isin"].isin(complete_isins)]
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

            filings = get_bse_filings(session, bse_scrip_code, max_filings=history)
            if not filings:
                stats.no_filings_isins.append(isin)
                continue
            if len(filings) < 2:
                # Keep the filed quarter.  The join needs two records and will
                # naturally leave this stock without a delta until the next
                # Reg. 31 filing arrives; it must not invent a zero quarter.
                # Single-filing ISINs keep their one real filing (quarterly
                # or interim) — never synthesize a second zero row.
                stats.no_filings_isins.append(isin)

            existing = {
                row[0]: {"mf_pct": row[1], "published_at": row[2]}
                for row in conn.execute(
                    "SELECT quarter_end, mf_pct, published_at "
                    "FROM shareholding_quarterly WHERE isin = ?",
                    (isin,),
                )
            }
            for filing in filings:
                stored = existing.get(filing["quarter_end"])
                if stored is not None and stored["mf_pct"] is not None:
                    if stored["published_at"] is None and filing.get("published_at"):
                        conn.execute(
                            "UPDATE shareholding_quarterly SET published_at = ? "
                            "WHERE isin = ? AND quarter_end = ?",
                            (filing["published_at"], isin, filing["quarter_end"]),
                        )
                        conn.commit()
                    stats.already_cached_records += 1
                    continue
                # One missing or unreadable filing must not cost the stock
                # its other quarters: record it and carry on.
                try:
                    response = session.get(f"{BSE_SITE_URL}{filing['attachment']}")
                    raw_bytes = getattr(response, "content", None)
                    if raw_bytes is None:
                        raw_bytes = response.text.encode("utf-8")
                    if isinstance(raw_bytes, str):
                        raw_bytes = raw_bytes.encode("utf-8")
                    # Persist raw iXBRL bytes before parsing and record sha256.
                    ixbrl_sha256 = _persist_ixbrl_attachment(cache_dir, bytes(raw_bytes))
                    parsed = parse_shareholding_document(bytes(raw_bytes))
                except (requests.RequestException, ShareholdingFetchError) as exc:
                    stats.failed_isins.append(f"{isin} {filing['quarter_end']}: {exc}")
                    continue
                record = {
                    "isin": isin,
                    "quarter_end": filing["quarter_end"],
                    **parsed,
                    "source": "bse_xbrl",
                    "filing_type": filing.get(
                        "filing_type",
                        classify_filing_type(filing["quarter_end"]),
                    ),
                    "ixbrl_sha256": ixbrl_sha256,
                    "source_url": f"{BSE_SITE_URL}{filing['attachment']}",
                    "published_at": filing.get("published_at"),
                }
                # The database is the cache keyed by (isin, quarter_end).
                # Persist immediately so an interrupted run never repeats a
                # successfully parsed filing on its next invocation.
                stats.loaded_records += db.load_shareholding_records(
                    conn, pd.DataFrame([record]),
                )
                existing[filing["quarter_end"]] = {
                    "mf_pct": parsed.get("mf_pct"), "published_at": filing.get("published_at"),
                }
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
        "--only-missing", action="store_true",
        help="Skip ISINs that already have two or more cached quarterly filings",
    )
    parser.add_argument(
        "--history", type=int, default=2, metavar="N",
        help="Newest N filings per stock (default 2; 0 = every filing back to 2015)",
    )
    parser.add_argument(
        "--universe", choices=("held", "nse"), default="held",
        help="held = ISINs tracked schemes hold (default); nse = every NSE-listed "
             "equity, the broader research universe for the quarterly backtest",
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
    if args.history < 0:
        parser.error("--history must not be negative")

    db_path = Path(args.db)
    conn = db.get_connection(str(db_path))
    cache_dir = db_path.parent / ".shareholding_cache"
    stats = fetch_shareholding(
        conn, PoliteSession(args.delay), cache_dir,
        limit=args.limit, only_missing=args.only_missing,
        history=args.history or None, universe=args.universe,
    )
    _print_stats(stats)

    if args.report_month:
        joined = consensus_signals.join_shareholding_increase(
            conn, consensus_signals.compute_consensus(conn, args.report_month),
            as_of_month=args.report_month,
        )
        common = joined[joined["is_common_with_fii_increase"]]
        print(f"\n=== MF + FII/DII common increases, {args.report_month} ===")
        print("(none)" if common.empty else common.to_string(index=False))


if __name__ == "__main__":
    main()
