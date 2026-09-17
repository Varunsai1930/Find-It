"""findit.store.validation_gate — pure validation checks. No DB/IO.

Every check returns {"passed": bool, "issues": [...]} where each issue is a
dict with at least {"code", "severity", "message"}. Severity is one of
"warn" (does not fail the gate), "error" (fails -> quarantine), "info".

Rules (per spec):
- NAV sum 95-105 passes; outside warns (partial sheets exist -> warn not fail).
- Implied-price CV across holders <1% passes; >=1% warns.
- Qty ratio outside 0.5-2.0 without a corporate action -> quarantine (fail).
- Promoter+public (or total of provided buckets) within 2 of 100 passes else fails.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Optional

NAV_MIN = 95.0
NAV_MAX = 105.0
PRICE_CV_WARN = 0.01
QTY_RATIO_LO = 0.5
QTY_RATIO_HI = 2.0
SHAREHOLDING_TOL = 2.0


def _issue(code: str, severity: str, message: str, **extra: Any) -> dict:
    out = {"code": code, "severity": severity, "message": message}
    out.update(extra)
    return out


def _coerce_nav_sum(nav_sum: Any) -> Optional[float]:
    """Accept a float total, iterable of values, Series, or DataFrame."""
    if nav_sum is None:
        return None
    # DataFrame with pct_nav column
    try:
        import pandas as pd  # local import: pure but pandas-friendly

        if isinstance(nav_sum, pd.DataFrame):
            if "pct_nav" not in nav_sum.columns:
                return None
            vals = pd.to_numeric(nav_sum["pct_nav"], errors="coerce").dropna()
            if len(vals) == 0:
                return None
            return float(vals.sum())
        if isinstance(nav_sum, pd.Series):
            vals = pd.to_numeric(nav_sum, errors="coerce").dropna()
            if len(vals) == 0:
                return None
            return float(vals.sum())
    except ImportError:
        pass
    if isinstance(nav_sum, (int, float)):
        try:
            f = float(nav_sum)
        except (TypeError, ValueError):
            return None
        if math.isnan(f):
            return None
        return f
    if isinstance(nav_sum, Mapping):
        v = nav_sum.get("pct_nav_sum", nav_sum.get("nav_sum", nav_sum.get("total")))
        if v is not None:
            return _coerce_nav_sum(v)
        return None
    if isinstance(nav_sum, Iterable) and not isinstance(nav_sum, (str, bytes)):
        try:
            nums = [float(v) for v in nav_sum]
        except (TypeError, ValueError):
            return None
        nums = [v for v in nums if not math.isnan(v)]
        if not nums:
            return None
        return float(sum(nums))
    return None


def validate_nav_sum(nav_sum: Any, *, label: str = "") -> dict:
    """NAV sum 95-105 passes; outside warns (never fails).

    Accepts a float total, an iterable of pct_nav values, or a DataFrame
    with a pct_nav column.
    """
    total = _coerce_nav_sum(nav_sum)
    prefix = f"{label}: " if label else ""
    if total is None:
        return {
            "passed": True,
            "issues": [
                _issue(
                    "nav_sum_missing",
                    "warn",
                    f"{prefix}no valid pct_nav values to sum (may be partial sheet)",
                )
            ],
        }
    if NAV_MIN <= total <= NAV_MAX:
        return {"passed": True, "issues": []}
    return {
        "passed": True,
        "issues": [
            _issue(
                "nav_sum_out_of_band",
                "warn",
                f"{prefix}pct_nav sum {total:.2f} outside 95-105 (may be partial sheet)",
                nav_sum=total,
            )
        ],
    }


def _compute_cv(values: Iterable[float]) -> Optional[float]:
    nums = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(f) or math.isinf(f):
            continue
        nums.append(f)
    if len(nums) < 2:
        return None
    mean = sum(nums) / len(nums)
    if mean == 0:
        return None
    var = sum((x - mean) ** 2 for x in nums) / len(nums)
    return math.sqrt(var) / abs(mean)


def validate_implied_price_cv(
    cv: Any = None,
    *,
    prices: Any = None,
    n_schemes: Any = None,
    label: str = "",
) -> dict:
    """Implied-price CV across holders: <1% passes, >=1% warns (never fails).

    Accepts either a precomputed cv float or a prices iterable. Single-holder
    (n<2) trivially passes.
    """
    prefix = f"{label}: " if label else ""
    value: Optional[float] = None
    if prices is not None:
        try:
            import pandas as pd

            if isinstance(prices, (pd.Series, pd.DataFrame)):
                if isinstance(prices, pd.DataFrame):
                    # expect implied_px column or first numeric column
                    col = (
                        "implied_px"
                        if "implied_px" in prices.columns
                        else prices.columns[0]
                    )
                    seq = pd.to_numeric(prices[col], errors="coerce").dropna().tolist()
                else:
                    seq = pd.to_numeric(prices, errors="coerce").dropna().tolist()
                value = _compute_cv(seq)
            else:
                seq = list(prices)  # type: ignore[arg-type]
                value = _compute_cv(seq)
        except ImportError:
            seq = list(prices)  # type: ignore[arg-type]
            value = _compute_cv(seq)
        if n_schemes is not None:
            try:
                if int(n_schemes) < 2:
                    return {"passed": True, "issues": []}
            except (TypeError, ValueError):
                pass
        if value is None:
            return {"passed": True, "issues": []}
    elif cv is not None:
        try:
            value = float(cv)
        except (TypeError, ValueError):
            return {"passed": True, "issues": []}
        if math.isnan(value):
            return {"passed": True, "issues": []}
        if n_schemes is not None:
            try:
                if int(n_schemes) < 2:
                    return {"passed": True, "issues": []}
            except (TypeError, ValueError):
                pass
    else:
        return {"passed": True, "issues": []}

    assert value is not None
    if value < PRICE_CV_WARN:
        return {"passed": True, "issues": []}
    return {
        "passed": True,
        "issues": [
            _issue(
                "price_cv_high",
                "warn",
                f"{prefix}implied-price CV {value:.4f} >= 1% across holders",
                cv=value,
            )
        ],
    }


# Backwards/forwards-compatible alias.
check_price_cv = validate_implied_price_cv


def validate_qty_ratio(
    qty_prev: Any,
    qty_curr: Any,
    has_corporate_action: bool = False,
    *,
    isin: str = "",
    label: str = "",
) -> dict:
    """Qty ratio outside 0.5-2.0 without corporate action -> quarantine (fail).

    New/exited positions (either side <= 0 / missing) skip the ratio check.
    """
    prefix = f"{label}: " if label else ""
    tag = f" {isin}" if isin else ""
    try:
        prev = float(qty_prev) if qty_prev is not None else None
        curr = float(qty_curr) if qty_curr is not None else None
    except (TypeError, ValueError):
        return {
            "passed": False,
            "issues": [
                _issue(
                    "qty_ratio_unreadable",
                    "error",
                    f"{prefix}unreadable quantities{tag}: prev={qty_prev!r} curr={qty_curr!r}",
                )
            ],
        }
    if prev is None or curr is None or math.isnan(prev) or math.isnan(curr):
        return {"passed": True, "issues": []}
    if prev <= 0 or curr <= 0:
        # new / exited / empty: no ratio semantics
        return {"passed": True, "issues": []}
    ratio = curr / prev
    if QTY_RATIO_LO <= ratio <= QTY_RATIO_HI:
        return {"passed": True, "issues": [], "ratio": ratio}
    if has_corporate_action:
        return {
            "passed": True,
            "issues": [
                _issue(
                    "qty_ratio_corporate_action",
                    "info",
                    f"{prefix}qty ratio {ratio:.3f}{tag} outside 0.5-2.0 but corporate action present",
                    ratio=ratio,
                )
            ],
            "ratio": ratio,
        }
    return {
        "passed": False,
        "issues": [
            _issue(
                "qty_ratio_out_of_band",
                "error",
                f"{prefix}qty ratio {ratio:.3f}{tag} outside 0.5-2.0 without corporate action -> quarantine",
                ratio=ratio,
            )
        ],
        "ratio": ratio,
    }


# Alias for discoverability.
check_qty_change = validate_qty_ratio


def _coerce_shareholding_total(
    promoter_pct: Any = None,
    public_pct: Any = None,
    fii_pct: Any = None,
    dii_pct: Any = None,
    record: Any = None,
) -> Optional[float]:
    if record is not None:
        if isinstance(record, Mapping):
            promoter_pct = record.get("promoter_pct", promoter_pct)
            public_pct = record.get("public_pct", public_pct)
            fii_pct = record.get("fii_pct", fii_pct)
            dii_pct = record.get("dii_pct", dii_pct)
        else:
            # DataFrame row / object with attributes
            try:
                import pandas as pd

                if isinstance(record, pd.Series):
                    promoter_pct = record.get("promoter_pct", promoter_pct)
                    public_pct = record.get("public_pct", public_pct)
                    fii_pct = record.get("fii_pct", fii_pct)
                    dii_pct = record.get("dii_pct", dii_pct)
            except ImportError:
                pass
    parts = []
    for v in (promoter_pct, public_pct, fii_pct, dii_pct):
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None  # signals invalid inputs
        if math.isnan(f):
            continue
        parts.append(f)
    if not parts:
        return None
    return float(sum(parts))


def validate_shareholding(
    promoter_pct: Any = None,
    public_pct: Any = None,
    fii_pct: Any = None,
    dii_pct: Any = None,
    record: Any = None,
    *,
    label: str = "",
) -> dict:
    """Promoter+public (or total of provided buckets) within 2 of 100.

    Sums every non-None bucket so both the shorthand (promoter+public) and
    the full (promoter+fii+dii+public) forms are checked against 100 +-2.
    Outside tolerance -> fail (quarantine).
    """
    prefix = f"{label}: " if label else ""
    # Detect invalid (non-numeric) inputs explicitly.
    for name, v in (
        ("promoter_pct", promoter_pct),
        ("public_pct", public_pct),
        ("fii_pct", fii_pct),
        ("dii_pct", dii_pct),
    ):
        if v is not None:
            try:
                f = float(v)
                if math.isnan(f):
                    continue
            except (TypeError, ValueError):
                return {
                    "passed": False,
                    "issues": [
                        _issue(
                            "shareholding_invalid",
                            "error",
                            f"{prefix}invalid shareholding input {name}={v!r}",
                        )
                    ],
                }
    if record is not None and isinstance(record, Mapping):
        for name in ("promoter_pct", "public_pct", "fii_pct", "dii_pct"):
            if name in record and record[name] is not None:
                try:
                    f = float(record[name])
                    if math.isnan(f):
                        continue
                except (TypeError, ValueError):
                    return {
                        "passed": False,
                        "issues": [
                            _issue(
                                "shareholding_invalid",
                                "error",
                                f"{prefix}invalid shareholding input {name}={record[name]!r}",
                            )
                        ],
                    }
    total = _coerce_shareholding_total(
        promoter_pct, public_pct, fii_pct, dii_pct, record
    )
    if total is None:
        return {
            "passed": True,
            "issues": [
                _issue(
                    "shareholding_missing",
                    "warn",
                    f"{prefix}no shareholding percentages provided",
                )
            ],
        }
    if abs(total - 100.0) <= SHAREHOLDING_TOL:
        return {"passed": True, "issues": []}
    return {
        "passed": False,
        "issues": [
            _issue(
                "shareholding_out_of_band",
                "error",
                f"{prefix}shareholding sums to {total:.2f}, expected 100 +-2",
                total=total,
            )
        ],
    }


check_shareholding = validate_shareholding


def validate_holdings_month(
    df_current: Any,
    df_prev: Any = None,
    corporate_action_isins: Any = None,
) -> dict:
    """Combined gate for one scheme-month batch (pure).

    - NAV sum warn-only.
    - Per-ISIN implied-price CV warn-only (when >=2 holders in df_current).
    - Per-ISIN qty ratio vs df_prev: error unless corporate action listed.
    """
    issues: list = []
    passed = True

    # NAV
    try:
        nav_res = validate_nav_sum(df_current)
        issues.extend(nav_res["issues"])
        # nav never fails
    except Exception:
        pass

    # Price CV per ISIN (needs market_value_lakhs + quantity columns)
    try:
        import pandas as pd

        if isinstance(df_current, pd.DataFrame) and {
            "isin",
            "quantity",
            "market_value_lakhs",
        } <= set(df_current.columns):
            tmp = df_current.copy()
            tmp["quantity"] = pd.to_numeric(tmp["quantity"], errors="coerce")
            tmp["market_value_lakhs"] = pd.to_numeric(
                tmp["market_value_lakhs"], errors="coerce"
            )
            tmp = tmp[(tmp["quantity"] > 0) & (tmp["market_value_lakhs"].notna())]
            if not tmp.empty:
                tmp["implied_px"] = tmp["market_value_lakhs"] / tmp["quantity"]
                for isin, grp in tmp.groupby("isin"):
                    if len(grp) >= 2:
                        r = validate_implied_price_cv(
                            prices=grp["implied_px"].tolist(), label=str(isin)
                        )
                        issues.extend(r["issues"])
    except ImportError:
        pass
    except Exception:
        pass

    # Qty ratios vs prev
    try:
        import pandas as pd

        ca = set(corporate_action_isins or [])
        if isinstance(df_current, pd.DataFrame) and df_prev is not None and isinstance(
            df_prev, pd.DataFrame
        ):
            if {"isin", "quantity"} <= set(df_current.columns) and {
                "isin",
                "quantity",
            } <= set(df_prev.columns):
                cur = df_current.groupby("isin")["quantity"].sum()
                prev = df_prev.groupby("isin")["quantity"].sum()
                for isin in set(cur.index) & set(prev.index):
                    r = validate_qty_ratio(
                        float(prev.loc[isin]),
                        float(cur.loc[isin]),
                        has_corporate_action=(str(isin) in ca),
                        isin=str(isin),
                    )
                    if not r["passed"]:
                        passed = False
                    issues.extend(r["issues"])
    except ImportError:
        pass
    except Exception:
        pass

    return {"passed": passed, "issues": issues}
