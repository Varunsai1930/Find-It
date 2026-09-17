"""findit.ingest — offline ingest helpers. No network, no tracker.db writes.

Pure helpers shared by the AMFI parser and the BSE shareholding fetcher:

- generic ISIN shape (keep foreign ISINs for later classification)
- NAV scale detection per scheme-month (fraction x100 / percent / warn+raw)
- filing_type classification (quarterly vs interim, keep both)
- FII label normalization + category extraction (no aggregate double-count)

These mirror the canonical implementations in amfi_mf_parser.py and
fetch_shareholding.py without importing them (stdlib only), so unit tests
stay offline and dependency-free. Repository persistence lives in
findit.store.repository and always uses tmp-path SQLite copies in tests.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

GENERIC_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")

NAV_FRACTION_RANGE = (0.95, 1.05)
NAV_PERCENT_RANGE = (95.0, 105.0)

QUARTERLY_MONTH_DAYS = frozenset({"03-31", "06-30", "09-30", "12-31"})

__all__ = [
    "GENERIC_ISIN_RE",
    "NAV_FRACTION_RANGE",
    "NAV_PERCENT_RANGE",
    "QUARTERLY_MONTH_DAYS",
    "is_generic_isin",
    "detect_nav_scale",
    "classify_filing_type",
    "normalize_fii_label",
    "fii_category",
    "compute_file_hash",
]


def is_generic_isin(isin: object) -> bool:
    """True for generic ISIN shape ^[A-Z]{2}[A-Z0-9]{10}$ (incl. foreign)."""
    if not isinstance(isin, str):
        return False
    return bool(GENERIC_ISIN_RE.match(isin.strip().upper()))


def detect_nav_scale(total: float) -> str:
    """Return fraction/percent/unknown for a scheme-month pct_nav sum.

    - 0.95..1.05 -> "fraction" (x100 to percent)
    - 95..105    -> "percent" (already canonical)
    - else       -> "unknown" (warn + keep raw, never force to 100)
    """
    try:
        t = float(total)
    except (TypeError, ValueError):
        return "unknown"
    if t != t:  # NaN
        return "unknown"
    if NAV_FRACTION_RANGE[0] <= t <= NAV_FRACTION_RANGE[1]:
        return "fraction"
    if NAV_PERCENT_RANGE[0] <= t <= NAV_PERCENT_RANGE[1]:
        return "percent"
    return "unknown"


def classify_filing_type(quarter_end: str) -> str:
    """Classify quarter_end as quarterly (03-31/06-30/09-30/12-31) vs interim."""
    s = str(quarter_end).strip()
    md: str | None = None
    # Prefer ISO YYYY-MM-DD slice; fall back to datetime parsing.
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        md = s[5:10]
    else:
        try:
            import datetime as _dt

            md = _dt.date.fromisoformat(s).strftime("%m-%d")
        except Exception:
            try:
                import pandas as _pd  # type: ignore

                md = _pd.Timestamp(s).strftime("%m-%d")
            except Exception:
                md = None
    return "quarterly" if md in QUARTERLY_MONTH_DAYS else "interim"


def normalize_fii_label(label: object) -> str:
    """Normalize an FII label: strip parens/hyphens, collapse spaces, lower."""
    s = str(label).replace("\xa0", " ").lower()
    for ch in ("(", ")", "-", "–", "—", "/", ","):
        s = s.replace(ch, " ")
    return " ".join(s.split())


_FPI_RE = re.compile(
    r"foreign\s+portfolio\s+investor[s]?\b(?:\s*category\s*(i{1,3}|1|2|3))?"
)
_FII_LEGACY_RE = re.compile(r"foreign\s+institutional\s+investor[s]?\b")


def fii_category(normalized_label: str) -> str | None:
    """Map a normalized FII label to i/ii/iii/aggregate/legacy, else None."""
    if _FII_LEGACY_RE.search(normalized_label):
        return "legacy"
    m = _FPI_RE.search(normalized_label)
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


def compute_file_hash(path: str | Path) -> str:
    """SHA256 hex of a file (used as source_file_hash provenance)."""
    h = hashlib.sha256()
    with open(str(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
