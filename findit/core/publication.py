"""When a disclosure became public. Pure; no DB/IO.

A fact dated June 30 is not known on June 30. A fund's June portfolio is
published up to ten days later, and a company's June-quarter shareholding
pattern up to 21 days later -- sometimes weeks after that. Anything that
tests a signal must only use what had been published by the decision date,
or it quietly trades on information nobody had.

Two rules, used everywhere:

- An *observed* publication timestamp (BSE's broadcast time) always wins.
- Without one, the regulatory deadline stands in for it. For MF portfolios
  that is conservative (SEBI requires publication by then). For shareholding
  patterns it is not -- companies file late -- so callers carry the basis
  alongside the date and report it.

A disclosure published on day D is first tradable at the close of the next
trading day after D: a 7pm broadcast cannot be traded at D's close.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

# SEBI: AMCs disclose scheme portfolios within 10 days of month end.
MF_DISCLOSURE_DAYS = 10
# SEBI LODR Reg. 31(1)(b): shareholding pattern within 21 days of quarter end.
SHAREHOLDING_FILING_DAYS = 21

BASIS_OBSERVED = "observed"
BASIS_DEADLINE = "regulatory_deadline"


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


def to_date(value) -> date:
    """date from a date, datetime or ISO string (date or timestamp)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"not an ISO date: {value!r}") from exc


def mf_disclosure_deadline(report_month: str) -> date:
    """Last day an AMC may publish report_month's portfolio."""
    return month_end(report_month) + timedelta(days=MF_DISCLOSURE_DAYS)


def shareholding_filing_deadline(quarter_end) -> date:
    """Last day a company may file the shareholding pattern for quarter_end."""
    return to_date(quarter_end) + timedelta(days=SHAREHOLDING_FILING_DAYS)


def shareholding_published(quarter_end, published_at) -> tuple[date, str]:
    """(publication date, basis) for one shareholding filing."""
    if published_at is not None and str(published_at).strip() not in ("", "nan", "None"):
        return to_date(published_at), BASIS_OBSERVED
    return shareholding_filing_deadline(quarter_end), BASIS_DEADLINE


def first_tradable_date(published) -> date:
    """Earliest calendar day whose close can act on a disclosure.

    The caller resolves this to the first *trading* day on or after it.
    """
    return to_date(published) + timedelta(days=1)


def mf_signal_entry_date(report_month: str) -> date:
    """Earliest day a month's MF consensus can be traded on."""
    return first_tradable_date(mf_disclosure_deadline(report_month))
