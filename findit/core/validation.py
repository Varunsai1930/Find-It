"""Pure validation helpers. No DB/IO."""

import pandas as pd


def check_duplicates(df, column: str = "isin") -> list:
    """Return issues for duplicated values in `column` (default 'isin')."""
    if df is None or not hasattr(df, "columns"):
        return ["missing dataframe for duplicates check"]
    if column not in df.columns:
        return []
    try:
        dup_mask = df.duplicated(subset=[column], keep=False)
    except Exception:
        return []
    dupes = df.loc[dup_mask, column].dropna().unique().tolist()
    if len(dupes) == 0:
        return []
    shown = ", ".join(str(d) for d in dupes[:5])
    suffix = "..." if len(dupes) > 5 else ""
    return [f"duplicates in {column}: {len(dupes)} value(s): {shown}{suffix}"]


def validate_scheme_month(df) -> list:
    """Validate one scheme-month with canonical pct_nav (percent scale).

    Issues:
      - pct_nav sum outside 95-105 warns (not error; partial sheets exist)
      - any pct_nav < 0 or > 50 flagged in a single line
      - duplicates via check_duplicates()
    """

    issues: list = []
    if df is None or not hasattr(df, "columns") or "pct_nav" not in df.columns:
        return ["missing pct_nav column"]
    try:
        vals = pd.to_numeric(df["pct_nav"], errors="coerce")
    except Exception:
        return ["unreadable pct_nav column"]
    valid = vals.dropna()
    if len(valid) == 0:
        return ["no valid pct_nav values"]

    total = float(valid.sum())
    if total < 95.0 or total > 105.0:
        issues.append(
            f"warn: pct_nav sum {total:.2f} outside 95-105 (may be partial sheet)"
        )

    bad = valid[(valid < 0) | (valid > 50)]
    if len(bad) > 0:
        issues.append(
            f"flag: {len(bad)} line(s) with pct_nav <0 or >50"
        )

    issues.extend(check_duplicates(df))
    return issues


def validate_shareholding(promoter, public) -> list:
    """Issue if abs(promoter + public - 100) > 2."""
    try:
        p = float(promoter)
        d = float(public)
    except (TypeError, ValueError):
        return ["invalid shareholding inputs"]
    total = p + d
    if abs(total - 100.0) > 2.0:
        return [f"shareholding sums to {total:.2f}, expected 100 +-2"]
    return []
