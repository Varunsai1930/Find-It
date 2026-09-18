"""
amfi_mf_parser.py

Parses ONE AMC's AMFI monthly portfolio disclosure Excel workbook into a
clean, normalized long-format table: one row per (scheme, stock) holding.

AMFI publishes these at:
  https://www.amfiindia.com/research-information/other-data/monthly-portfolio-disclosures
Each AMC publishes one workbook per month, with one sheet per scheme it runs.

USAGE:
  python amfi_mf_parser.py path/to/hdfc_march_2026.xlsx --amc "HDFC AMC" --month 2026-03

WHY THIS IS WRITTEN DEFENSIVELY:
SEBI prescribes the same columns everywhere (Name of the Instrument, ISIN,
Industry, Quantity, Market Value (Rs. Lakhs), % to NAV) but AMCs are
inconsistent about exact naming, casing, and extra columns. This parser
matches columns by a synonym list instead of fixed names, and FAILS LOUDLY
(raises) when it can't confidently match a required column, instead of
silently skipping or misassigning it. A wrong ISIN or quantity is a much
worse failure than a crash you can see and fix.

WHAT THIS DOES NOT DO YET:
- Doesn't download from AMFI itself (network in this environment can't
  reach amfiindia.com) — point it at a file you've already downloaded.
- Doesn't loop over multiple AMCs/months — that's a thin wrapper around
  this once this is confirmed working against 2-3 real files.
- COLUMN_SYNONYMS below is seeded from the SEBI-prescribed column names
  and public descriptions of the format, NOT tested against a real AMFI
  file yet, since I can't fetch one from this sandbox. Treat it as a
  first draft to validate against an actual downloaded file (see the
  note at the bottom of this file).
"""

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

# Known/likely column name variants seen across AMCs.
# EXTEND THIS as you hit real files that don't match — that's expected
# and is the normal way this list grows. Never silently work around a
# miss; add the real column name here instead.
COLUMN_SYNONYMS = {
    "isin": ["isin", "isin no", "isin number", "isin code"],
    "instrument_name": [
        "name of the instrument",
        "name of instrument",
        "instrument name",
        "security name",
    ],
    "industry": ["industry", "industry / rating", "sector", "industry+/rating"],
    "quantity": ["quantity", "qty", "no of shares", "no. of shares"],
    "market_value_lakhs": [
        "market value",
        "market value (rs. in lakhs)",
        "market value (rs in lakhs)",
        "market/fair value(rs. in lakhs)",
        "market value(rs in lacs)",
        "market value (rs lacs)",
        # observed in real HDFC disclosures ("Market/ Fair Value (Rs. in Lacs.)")
        "market/ fair value (rs. in lacs.)",
        # observed in real ICICI Prudential disclosures ("Exposure/Market Value(Rs.Lakh)")
        "exposure/market value(rs.lakh)",
    ],
    "pct_nav": [
        "% to nav",
        "% to net assets",
        "percentage to nav",
        "% to net asset",
        "% to aum",
    ],
}

REQUIRED = ["isin", "instrument_name", "quantity", "market_value_lakhs", "pct_nav"]

# Generic ISIN shape (2-letter country + 10 alphanumerics). Indian holdings
# match ^IN..., foreign holdings (e.g. US...) match the generic shape and are
# KEPT as instruments for later classification (db.classify_isin -> foreign)
# instead of being silently dropped. Only rows failing the generic shape
# (subtotals, notes, garbage) are dropped — with a reported count.
ISIN_GENERIC_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")

# Per scheme-month NAV scale bands. Fraction sheets sum ~1.0, percent sheets
# sum ~100. Anything else warns and keeps raw — never forced to 100.
NAV_FRACTION_RANGE = (0.95, 1.05)
NAV_PERCENT_RANGE = (95.0, 105.0)


def normalize_col(col) -> str:
    return " ".join(str(col).strip().lower().split())


def match_columns(raw_columns) -> dict:
    """Map raw column names to canonical names. Raises if a required
    canonical column has no confident match anywhere in raw_columns."""
    normalized = {normalize_col(c): c for c in raw_columns}
    mapping = {}
    for canonical, synonyms in COLUMN_SYNONYMS.items():
        found = None
        for syn in synonyms:
            if syn in normalized:
                found = normalized[syn]
                break
        if found is None:
            # fallback: substring match, still explicit
            for norm_col, raw_col in normalized.items():
                if any(syn in norm_col for syn in synonyms):
                    found = raw_col
                    break
        if found is None and canonical in REQUIRED:
            raise ValueError(
                f"Could not find a column for '{canonical}' among {raw_columns}. "
                f"Add the real column name to COLUMN_SYNONYMS['{canonical}'] "
                f"in this file and retry -- do not guess."
            )
        if found:
            mapping[canonical] = found
    return mapping


def detect_nav_scale(total: float) -> str:
    """Classify a scheme-month pct_nav sum into fraction/percent/unknown.

    - 0.95..1.05  -> "fraction" (values must be x100 to reach percent scale)
    - 95..105     -> "percent" (already canonical)
    - else        -> "unknown" (caller warns and keeps raw, never forces 100)
    """
    try:
        t = float(total)
    except (TypeError, ValueError):
        return "unknown"
    # NaN never matches a band.
    if t != t:
        return "unknown"
    if NAV_FRACTION_RANGE[0] <= t <= NAV_FRACTION_RANGE[1]:
        return "fraction"
    if NAV_PERCENT_RANGE[0] <= t <= NAV_PERCENT_RANGE[1]:
        return "percent"
    return "unknown"


def dropped_holding_mask(clean: pd.DataFrame, valid_mask: pd.Series) -> pd.Series:
    """Identify non-ISIN detail rows without counting printed totals twice.

    Section labels/notes normally have no numeric holding cells. Summary
    labels may have all three numeric cells, so explicitly exclude those.
    A real omitted detail row must have a name and at least one numeric cell.
    """
    names = clean["instrument_name"].fillna("").astype(str).str.strip()
    summaries = names.str.contains(
        r"\b(?:sub\s*[- ]?total|total|net\s+assets?|net\s+asset\s+value)\b",
        case=False, regex=True,
    )
    numeric = clean[["quantity", "market_value_lakhs", "pct_nav"]].apply(
        pd.to_numeric, errors="coerce"
    ).notna().any(axis=1)
    # Disclosures often append derivative exposure tables and numeric
    # footnotes after the portfolio grand total, reusing these column
    # positions for contract counts or notionals rather than NAV weights.
    # They must not be counted as additional omitted portfolio positions.
    after_portfolio = names.str.match(r"grand\s*total\b", case=False).cummax()
    return ~valid_mask & names.ne("") & ~summaries & numeric & ~after_portfolio


def parse_workbook(path: Path, amc_name: str, report_month: str) -> pd.DataFrame:
    """Parse holdings and repeat exclusion provenance on each scheme's rows.

    ``pct_nav_scale`` describes the source scale; ``pct_nav`` and
    ``dropped_non_isin_pct_nav`` share the resulting canonical scale. An
    unknown scale stays raw. A scheme with only omitted detail rows has one
    blank-ISIN metadata row, which consumers must never insert as a holding.
    """
    xls = pd.ExcelFile(path)
    all_rows = []

    for sheet_name in xls.sheet_names:
        # SINGLE READ per sheet: headerless, then slice the header row
        # in-memory. Never re-read the same sheet with header=<idx>.
        raw = pd.read_excel(xls, sheet_name=sheet_name, header=None)

        # AMFI sheets usually have a few title/metadata rows before the
        # real header row. Find it by locating the first row containing
        # something that matches an ISIN column synonym.
        header_row_idx = None
        scan_rows = min(10, len(raw))
        for i in range(scan_rows):
            row_vals = [normalize_col(v) for v in raw.iloc[i].tolist()]
            if any(syn in row_vals for syn in COLUMN_SYNONYMS["isin"]):
                header_row_idx = i
                break

        if header_row_idx is None:
            print(
                f"  [skip] '{sheet_name}': no recognizable header row -- "
                f"likely a notes/disclaimer sheet, not a scheme. Skipping.",
                file=sys.stderr,
            )
            continue

        header_vals = raw.iloc[header_row_idx].tolist()
        df = raw.iloc[header_row_idx + 1:].copy()
        df.columns = header_vals
        df = df.reset_index(drop=True)
        df = df.dropna(how="all")

        try:
            col_map = match_columns(list(df.columns))
        except ValueError as e:
            print(f"  [FAIL] sheet '{sheet_name}': {e}", file=sys.stderr)
            raise

        # Log the resolved canonical -> raw mapping for auditability.
        print(f"  [columns] '{sheet_name}': {col_map}", file=sys.stderr)

        keep_cols = [c for c in REQUIRED if c in col_map] + (
            ["industry"] if "industry" in col_map else []
        )
        clean = df.rename(columns={v: k for k, v in col_map.items()})
        clean = clean[keep_cols]

        # ISIN hygiene: keep every row matching the generic ISIN shape
        # (Indian IN... plus foreign US.../etc. for later classification).
        # Drop + REPORT only rows failing the generic regex (subtotal/total/
        # blank/note rows) instead of silently dropping them.
        isin_norm = clean["isin"].astype(str).str.strip().str.upper()
        valid_mask = isin_norm.str.match(ISIN_GENERIC_RE, na=False)
        dropped_mask = dropped_holding_mask(clean, valid_mask)
        dropped_count = int(dropped_mask.sum())
        dropped_nav = pd.to_numeric(
            clean.loc[dropped_mask, "pct_nav"], errors="coerce"
        ).sum(min_count=1) if dropped_count else 0.0
        n_bad = int((~valid_mask).sum())
        n_kept = int(valid_mask.sum())
        if n_bad:
            print(
                f"  [isin] '{sheet_name}': {n_bad} row(s) failing ISIN regex "
                f"dropped ({dropped_count} holding detail rows); "
                f"kept {n_kept} row(s) with generic ISIN "
                f"(incl. foreign for later classification).",
                file=sys.stderr,
            )
        else:
            print(
                f"  [isin] '{sheet_name}': 0 rows failing ISIN regex; "
                f"kept {n_kept}.",
                file=sys.stderr,
            )
        clean = clean.loc[valid_mask].copy()
        clean["isin"] = isin_norm.loc[valid_mask].values

        if clean.empty and dropped_count:
            # CSV-compatible metadata carrier, never a holding. The loader
            # registers this scheme/provenance, then skips the blank ISIN.
            clean = pd.DataFrame([{column: None for column in keep_cols}])
        clean["dropped_non_isin_count"] = dropped_count
        clean["dropped_non_isin_pct_nav"] = dropped_nav

        clean = clean.copy()
        clean["scheme_name"] = sheet_name.strip()
        clean["amc_name"] = amc_name
        clean["report_month"] = report_month
        all_rows.append(clean)

    if not all_rows:
        raise ValueError(f"No parseable scheme sheets found in {path}")

    result = pd.concat(all_rows, ignore_index=True)
    result["quantity"] = pd.to_numeric(result["quantity"], errors="coerce")
    result["market_value_lakhs"] = pd.to_numeric(
        result["market_value_lakhs"], errors="coerce"
    )
    # Retain the raw NAV weight and derive the canonical percent column via
    # per scheme-month scale detection. Never force an ambiguous sum to 100.
    result["pct_nav_raw"] = pd.to_numeric(result["pct_nav"], errors="coerce")
    result["pct_nav"] = result["pct_nav_raw"]

    for (amc, scheme, month), idx in result.groupby(
        ["amc_name", "scheme_name", "report_month"]
    ).groups.items():
        accepted_total = result.loc[idx, "pct_nav_raw"].sum(min_count=1)
        omitted_total = result.loc[idx, "dropped_non_isin_pct_nav"].iloc[0]
        total = float(pd.Series([accepted_total, omitted_total]).sum(min_count=1))
        scale = detect_nav_scale(total)
        result.loc[idx, "pct_nav_scale"] = scale
        if scale == "fraction":
            result.loc[idx, "pct_nav"] = result.loc[idx, "pct_nav_raw"] * 100.0
            result.loc[idx, "dropped_non_isin_pct_nav"] *= 100.0
            print(
                f"  [nav-scale] '{scheme}' [{month}]: sum {total:.4f} in "
                f"0.95-1.05 -> fraction x100 to percent.",
                file=sys.stderr,
            )
        elif scale == "percent":
            print(
                f"  [nav-scale] '{scheme}' [{month}]: sum {total:.2f} in "
                f"95-105 -> percent, kept as-is.",
                file=sys.stderr,
            )
        else:
            print(
                f"  [warn] '{scheme}' [{month}]: pct_nav sum {total:.4f} "
                f"outside 0.95-1.05 and 95-105; keeping raw, never "
                f"forcing to 100.",
                file=sys.stderr,
            )

    bad_rows = result[
        result["isin"].notna()
        & result[["quantity", "market_value_lakhs", "pct_nav_raw"]].isna().any(axis=1)
    ]
    if len(bad_rows):
        print(
            f"  [warn] {len(bad_rows)} row(s) had a numeric value that didn't "
            f"parse -- inspect these manually, don't silently drop them:",
            file=sys.stderr,
        )
        print(bad_rows.to_string(), file=sys.stderr)

    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", type=Path, help="Path to one AMC's monthly disclosure .xlsx")
    ap.add_argument("--amc", required=True, help="AMC name, e.g. 'HDFC AMC'")
    ap.add_argument("--month", required=True, help="Report month, e.g. 2026-08")
    ap.add_argument("--out", type=Path, default=None, help="Output CSV path")
    args = ap.parse_args()

    print(f"Parsing {args.file} ...")
    df = parse_workbook(args.file, args.amc, args.month)
    print(
        f"Parsed {df['isin'].notna().sum()} holding rows across {df['scheme_name'].nunique()} "
        f"schemes."
    )

    out_path = args.out or args.file.with_suffix(".parsed.csv")
    df.to_csv(out_path, index=False)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------
# NEXT STEP -- this needs a real file to prove itself:
# Download one AMC's latest monthly disclosure from
#   amfiindia.com -> Research & Information -> Other Data ->
#   Monthly Portfolio Disclosures
# (pick a large AMC like HDFC or SBI first -- more likely to be a clean
# example) and either run this script on it locally, or upload the .xlsx
# here and I'll run it against the real file and fix COLUMN_SYNONYMS
# against whatever it actually contains.
# ---------------------------------------------------------------------------
