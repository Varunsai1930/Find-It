"""Versioned comparisons and validation of the inputs consumed by legacy replay."""
from __future__ import annotations

import math
from datetime import date, timedelta

from findit.core.rules import LEGACY_RULE_VERSION, RULE_VERSION, require_supported_rules


class LegacyReplayError(ValueError):
    """An old result would conceal an unavailable input."""


def comparison_for_rules(conn, month, active_only=True, rules_version=RULE_VERSION):
    require_supported_rules(rules_version)
    if rules_version == LEGACY_RULE_VERSION:
        from findit.core.legacy_calculations import comparison_rows
    else:
        from findit.core.consensus_signals import comparison_rows
    return comparison_rows(conn, month, active_only)


def previous_month(month):
    return (date.fromisoformat(month + "-01") - timedelta(days=1)).strftime("%Y-%m")


def guard_legacy_frame(frame):
    """Stored flows and split arithmetic are consumed inputs too."""
    for key in ("quantity_prev", "quantity_curr", "qty_change", "flow_lakhs"):
        if key in frame and any(not isinstance(v, (int, float)) or not math.isfinite(v)
                                for v in frame[key]):
            raise LegacyReplayError(
                "This legacy request contains unavailable numeric inputs. "
                "Open the corrected successor release to see availability explicitly.")


def guard_legacy_inputs(conn, scheme_ids, months, stocks, *, include_nav=False):
    """Only safe consumed legacy inputs can be reproduced with the old rules.

    All domestic quantities in a compared snapshot determine its eligibility.
    Value and NAV fields are checked for the requested securities; missing NAV
    does not invalidate a watchlist or history that never consumes it.
    """
    ids, months, stocks = set(scheme_ids), set(months), set(stocks)
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    periods = ",".join("?" for _ in months)
    rows = conn.execute(
        "SELECT h.isin,h.quantity,h.market_value_lakhs,h.pct_nav "
        "FROM mf_holdings_monthly h JOIN stocks s USING(isin) "
        f"WHERE s.instrument_type='equity' AND h.scheme_id IN ({placeholders}) "
        f"AND h.report_month IN ({periods})", (*sorted(ids), *sorted(months)))
    for isin, quantity, value, nav in rows:
        fields = [quantity]
        if isin in stocks:
            fields.append(value)
            if include_nav:
                fields.append(nav)
        if any(v is None or not isinstance(v, (int, float)) or
               not math.isfinite(v) or v < 0 for v in fields):
            raise LegacyReplayError(
                "This legacy request contains unavailable numeric inputs. "
                "Open the corrected successor release to see availability explicitly.")
