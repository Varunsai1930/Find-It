"""
Parses ONE AMC's AMFI monthly portfolio disclosure Excel workbook into a
clean, normalized long-format table: one row per (scheme, stock) holding.

AMFI publishes these at:
  https://www.amfiindia.com/research-information/other-data/monthly-portfolio-disclosures
Each AMC publishes one workbook per month, with one sheet per scheme it runs.

USAGE (one workbook; ``findit.cli.intake`` checks and parses a whole month):
  python3 -m findit.cli.parse path/to/hdfc_march_2026.xlsx --amc "HDFC AMC" --month 2026-03

WHY THIS IS WRITTEN DEFENSIVELY:
SEBI prescribes the same columns everywhere (Name of the Instrument, ISIN,
Industry, Quantity, Market Value (Rs. Lakhs), % to NAV) but AMCs are
inconsistent about exact naming, casing, and extra columns. This parser
matches columns by a synonym list instead of fixed names, and FAILS LOUDLY
(raises) when it can't confidently match a required column, instead of
silently skipping or misassigning it. A wrong ISIN or quantity is a much
worse failure than a crash you can see and fix.
"""

import re
import sys
import hashlib
import json
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
        # observed in real Nippon India disclosures ("Market/Fair Value\n( Rs. in Lacs)")
        "market/fair value ( rs. in lacs)",
        # observed in real Baroda BNP Paribas and PPFAS disclosures
        # ("Market/Fair Value\n (Rs. in Lakhs)")
        "market/fair value (rs. in lakhs)",
        # Mirae Asset monthly workbooks ("Market/Fair Value\n(Rs. in Lacs)")
        "market/fair value (rs. in lacs)",
        # UTI consolidated exposure workbook
        "market-value",
    ],
    "pct_nav": [
        "% to nav",
        "% to net assets",
        "percentage to nav",
        # HSBC July/August 2026 originals; use the existing scale detector.
        "percentage to net assets",
        "% to net asset",
        "% to aum",
    ],
}

REQUIRED = ["isin", "instrument_name", "quantity", "market_value_lakhs", "pct_nav"]
PARSER_VERSION = "portfolio-2026-10-02-scope-1"

# Generic ISIN shape (ISO 6166: 2-letter country + 9 alphanumerics + a numeric
# check digit). Indian holdings match ^IN..., foreign holdings (e.g. US...)
# match the generic shape and are KEPT as instruments for later
# classification (db.classify_isin -> foreign) instead of being silently
# dropped. Only rows failing the generic shape are dropped -- with a reported
# count: subtotals, notes, industry-summary rows whose label happens to be 12
# letters ("CONSTRUCTION"), and AMC placeholders for securities without an
# ISIN ("EQ600401XXXX").
ISIN_GENERIC_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")

# Per scheme-month NAV scale bands. Fraction sheets sum ~1.0, percent sheets
# sum ~100. Anything else warns and keeps raw — never forced to 100.
NAV_FRACTION_RANGE = (0.95, 1.05)
NAV_PERCENT_RANGE = (95.0, 105.0)


# Title rows above the header that are not the scheme's name.
_NOT_A_TITLE_RE = re.compile(
    r"^(?:portfolio\b|monthly portfolio|back to index$|index$|scheme name\s*:?$|"
    r"(?:.*\s)?mutual fund$|as on\b|statement of|\d"
    # A single word with a digit is a scheme code (Nippon's "RLMF001"), not a name.
    r"|[a-z]*\d[a-z0-9]*$)",
    re.IGNORECASE,
)


def extract_scheme_title(title_rows: pd.DataFrame):
    """The scheme's full name from the rows above the holdings header, or None.

    Sheets are often named by code ("SAOF", "NIF30DEX"); the header rows
    carry the real name ("SBI Arbitrage Fund"), which is what tells an
    index or arbitrage fund apart from an active one. Layouts seen:
    SBI "SCHEME NAME :" | name; ICICI a bare name under the AMC line;
    HDFC the name (with a SEBI description in brackets) in row 0.
    """
    cells_by_row = [
        [line.strip() for v in row if pd.notna(v) and not hasattr(v, "year")
         for line in str(v).splitlines() if line.strip()]
        for row in title_rows.itertuples(index=False, name=None)
    ]
    for cells in cells_by_row:
        for i, cell in enumerate(cells):
            # Kotak places the name inside a dated portfolio title.
            portfolio = re.match(r"^portfolio of (.+?)\s+as on\s+.+$", cell, re.IGNORECASE)
            if portfolio:
                return portfolio.group(1).strip()
            if re.match(r"^scheme\s*name\s*:?", cell, re.IGNORECASE):
                rest = re.sub(r"^scheme\s*name\s*:?\s*", "", cell, flags=re.IGNORECASE)
                if rest:
                    return rest
                return cells[i + 1] if i + 1 < len(cells) else None
    for cells in cells_by_row:
        for cell in cells:
            if len(cell) > 3 and not _NOT_A_TITLE_RE.match(cell):
                return " ".join(cell.split())
    return None


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
    after_portfolio = names.str.match(
        r"(?:grand\s*total|total\s+net\s+assets|net\s+assets|net\s+asset\s+value)\b",
        case=False,
    ).cummax()
    # These printed category totals contain no instrument quantity; their
    # constituents are the detail rows that follow them.
    categories = names.str.fullmatch(
        r"(?:equity & equity related instruments(?: \(.*\))?|debt instruments|"
        r"listed / awaiting listing on stock exchanges|government securities|"
        r"non-convertible debentures / bonds|securitized debt instruments|"
        r"money market instruments|certificate of deposits|treasury bills|"
        r"zero coupon bonds / deep discount bonds|privately placed/unlisted|"
        r"units of real estate investment trust \(REITs\)|"
        r"units of infrastructure investment trusts \(InvITs\))",
        case=False,
    ) & clean["quantity"].isna()
    return ~valid_mask & names.ne("") & ~summaries & ~categories & numeric & ~after_portfolio


def _reported_nav_total(clean: pd.DataFrame) -> float | None:
    """Use an explicitly printed portfolio total as source-scale evidence.

    A partial portfolio's summed rows may not reach one or 100. Category
    subtotals can also repeat its weight. The portfolio total identifies
    units without guessing from an individual stock's weight. Conflicting
    totals stay ambiguous and fall back to the existing row-sum rule.
    """
    names = clean["instrument_name"].fillna("").astype(str).str.strip()
    is_total = names.str.fullmatch(
        r"grand\s+total|total\s+net\s+assets|net\s+assets|net\s+asset\s+value",
        case=False,
    )
    values = pd.to_numeric(clean.loc[is_total, "pct_nav"], errors="coerce").dropna()
    if values.empty:
        return None
    total = float(values.iloc[0])
    if detect_nav_scale(total) == "unknown" or (values - total).abs().gt(1e-6).any():
        return None
    return total


def find_header_row(raw: pd.DataFrame):
    """Index of the first row (within the first 10) naming an ISIN column."""
    for i in range(min(10, len(raw))):
        row_vals = [normalize_col(v) for v in raw.iloc[i].tolist()]
        if any(syn in row_vals for syn in COLUMN_SYNONYMS["isin"]):
            return i
    return None


def read_scheme_titles(path: Path) -> dict:
    """{sheet name: full scheme title or None} for every holdings sheet."""
    xls = pd.ExcelFile(path)
    titles = {}
    for sheet_name in xls.sheet_names:
        raw = pd.read_excel(xls, sheet_name=sheet_name, header=None, nrows=12)
        header_row_idx = find_header_row(raw)
        if header_row_idx is not None:
            titles[sheet_name.strip()] = extract_scheme_title(raw.iloc[:header_row_idx])
    return titles


def _scheme_sheets(xls):
    """Yield ordinary sheets or UTI's consecutive scheme sections as sheets."""
    for sheet_name in xls.sheet_names:
        raw = pd.read_excel(xls, sheet_name=sheet_name, header=None)
        first = str(raw.iat[0, 0]).strip() if not raw.empty else ""
        if not re.fullmatch(r"SCHEME CODE\d+STARTS", first):
            yield sheet_name, raw
            continue
        start = None
        code = None
        for index, value in raw.iloc[:, 0].items():
            marker = str(value).strip()
            opening = re.fullmatch(r"SCHEME CODE(\d+)STARTS", marker)
            closing = re.fullmatch(r"SCHEME CODE(\d+)ENDS", marker)
            if opening:
                if start is not None:
                    raise ValueError("UTI portfolio has nested scheme sections")
                start, code = index, opening.group(1)
            elif closing:
                if start is None or closing.group(1) != code:
                    raise ValueError("UTI portfolio has an unmatched scheme end")
                section = raw.iloc[start + 1:index].reset_index(drop=True)
                section.attrs["source_row_offset"] = start + 1
                section.attrs["source_sheet"] = sheet_name
                titles = section.iloc[:5, 0]
                names = [str(v).split(":", 1)[1].strip() for v in titles
                         if str(v).startswith("SCHEME:")]
                if len(names) != 1:
                    raise ValueError(f"UTI scheme section {code} has no unique title")
                yield names[0], section
                start, code = None, None
        if start is not None:
            raise ValueError(f"UTI portfolio has an unclosed scheme section {code}")


def parse_workbook(path: Path, amc_name: str, report_month: str) -> pd.DataFrame:
    """Parse holdings and repeat exclusion provenance on each scheme's rows.

    ``pct_nav_scale`` describes the source scale; ``pct_nav`` and
    ``dropped_non_isin_pct_nav`` share the resulting canonical scale. An
    unknown scale stays raw. A scheme with only omitted detail rows has one
    blank-ISIN metadata row, which consumers must never insert as a holding.
    """
    xls = pd.ExcelFile(path)
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    source_metadata = {}
    manifest = path.parent / ".findit-download.json"
    if manifest.is_file():
        source_metadata = next((r for r in json.loads(manifest.read_text()).get("files", [])
                                if r.get("sha256") == source_hash), {})
    all_rows = []
    reported_totals = {}

    for sheet_name, raw in _scheme_sheets(xls):
        # Each workbook sheet is read once; UTI's stacked sections are sliced
        # from that read in memory.

        if (amc_name == "Kotak Mahindra AMC" and sheet_name == "Common Notes"
                and not raw.empty and str(raw.iat[0, 0]).strip() == "Common Notes to Portfolio:"):
            # Recovery values across matured schemes have no holding quantities.
            # Retain the original supplementary table, without loading a portfolio.
            print("  [skip] 'Common Notes': Kotak supplementary recovery disclosure",
                  file=sys.stderr)
            continue

        # AMFI sheets usually have a few title/metadata rows before the
        # real header row. Find it by locating the first row containing
        # something that matches an ISIN column synonym.
        header_row_idx = find_header_row(raw)

        if header_row_idx is None:
            print(
                f"  [skip] '{sheet_name}': no recognizable header row -- "
                f"likely a notes/disclaimer sheet, not a scheme. Skipping.",
                file=sys.stderr,
            )
            continue

        scheme_title = extract_scheme_title(raw.iloc[:header_row_idx])
        header_vals = raw.iloc[header_row_idx].tolist()
        df = raw.iloc[header_row_idx + 1:].copy()
        df.columns = header_vals
        df["source_row"] = df.index + 1 + raw.attrs.get("source_row_offset", 0)
        df = df.reset_index(drop=True)
        df = df.dropna(how="all")

        try:
            col_map = match_columns(list(df.columns))
        except ValueError as e:
            print(f"  [FAIL] sheet '{sheet_name}': {e}", file=sys.stderr)
            raise

        if (amc_name == "Kotak Mahindra AMC" and len(header_vals) >= 4
                and normalize_col(header_vals[0]) == "name of instrument"
                and pd.isna(header_vals[1]) and pd.isna(header_vals[2])
                and normalize_col(header_vals[3]) == "isin code"):
            # A:C is merged in the heading; security names are in C and
            # section/subtotal labels in A/B. Preserve both kinds of labels.
            section_columns = df.iloc[:, :2].copy()
            df[col_map["instrument_name"]] = df.iloc[:, :3].ffill(axis=1).iloc[:, -1]
            # This layout prints totals in the Industry column, and appends
            # separate exposure tables below Grand Total. Stock ISINs in a
            # Futures section identify the underlying, not shares held.
            labels = df.iloc[:, 4].fillna("").astype(str).str.strip()
            totals = labels.str.fullmatch(r"(?:grand )?total", case=False)
            df.loc[totals, col_map["instrument_name"]] = labels.loc[totals]
            derivative, finished, excluded = False, False, []
            for idx, row in df.iterrows():
                sections = section_columns.loc[idx]
                heading = next((str(v).strip() for v in sections
                                if pd.notna(v) and str(v).strip()), "")
                if heading:
                    derivative = heading.lower() in {"futures", "options", "derivatives"}
                if finished or derivative:
                    excluded.append(idx)
                if str(row[col_map["instrument_name"]]).strip().lower() == "grand total":
                    finished = True
            if excluded:
                print(f"  [scope] '{sheet_name}': excluded derivative/exposure rows "
                      f"at source rows {df.loc[excluded, 'source_row'].tolist()}; "
                      "original workbook retained", file=sys.stderr)
                df = df.drop(index=excluded)

        # Log the resolved canonical -> raw mapping for auditability.
        print(f"  [columns] '{sheet_name}': {col_map}", file=sys.stderr)

        keep_cols = [c for c in REQUIRED if c in col_map] + (
            ["industry"] if "industry" in col_map else []
        )
        clean = df.rename(columns={v: k for k, v in col_map.items()})
        clean = clean[keep_cols + ["source_row"]]
        clean["source_raw_json"] = [json.dumps({k: None if pd.isna(row[k]) else str(row[k])
                                               for k in keep_cols}, sort_keys=True)
                                    for _, row in clean.iterrows()]

        # ISIN hygiene: keep every row matching the generic ISIN shape
        # (Indian IN... plus foreign US.../etc. for later classification).
        # Drop + REPORT only rows failing the generic regex (subtotal/total/
        # blank/note rows) instead of silently dropping them.
        isin_norm = clean["isin"].astype(str).str.strip().str.upper()
        valid_mask = isin_norm.str.match(ISIN_GENERIC_RE, na=False)
        dropped_mask = dropped_holding_mask(clean, valid_mask)
        reported_total = _reported_nav_total(clean)
        reported_totals[sheet_name.strip()] = reported_total
        dropped_count = int(dropped_mask.sum())
        dropped_nav = pd.to_numeric(
            clean.loc[dropped_mask, "pct_nav"], errors="coerce"
        ).sum(min_count=1) if dropped_count else 0.0
        if reported_total is not None and dropped_count:
            # Reconcile against the printed total rather than double-counting
            # category headings and detail rows in the omitted weight.
            accepted = pd.to_numeric(clean.loc[valid_mask, "pct_nav"], errors="coerce")
            accepted_sum = float(accepted.sum())
            if (accepted.notna().all() and pd.notna(dropped_nav)
                    and accepted_sum <= reported_total):
                dropped_nav = reported_total - accepted_sum
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
            clean = pd.DataFrame([{column: None for column in
                                   keep_cols + ["source_row", "source_raw_json"]}])
        clean["dropped_non_isin_count"] = dropped_count
        clean["dropped_non_isin_pct_nav"] = dropped_nav

        clean = clean.copy()
        clean["scheme_name"] = sheet_name.strip()
        clean["scheme_title"] = scheme_title
        clean["amc_name"] = amc_name
        clean["report_month"] = report_month
        clean["source_sha256"] = source_hash
        clean["source_workbook"] = path.name
        clean["source_sheet"] = raw.attrs.get("source_sheet", sheet_name)
        clean["source_url"] = source_metadata.get("source_url")
        clean["source_retrieved_at"] = source_metadata.get("retrieved_at")
        clean["source_published_at"] = source_metadata.get("published_at")
        clean["parser_version"] = PARSER_VERSION
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
        reported_total = reported_totals[scheme]
        scale = detect_nav_scale(reported_total if reported_total is not None else total)
        result.loc[idx, "pct_nav_scale"] = scale
        if scale == "fraction":
            result.loc[idx, "pct_nav"] = result.loc[idx, "pct_nav_raw"] * 100.0
            result.loc[idx, "dropped_non_isin_pct_nav"] *= 100.0
            print(
                f"  [nav-scale] '{scheme}' [{month}]: sum {total:.4f} in "
                f"-> fraction x100 to percent (printed total {reported_total}).",
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
