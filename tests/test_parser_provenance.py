"""Synthetic disclosures exercise exclusion accounting without real-data writes."""

import pandas as pd
import pytest

from amfi_mf_parser import parse_workbook


HEADERS = ["Name of the Instrument", "ISIN", "Quantity", "Market Value", "% to NAV"]


def workbook(tmp_path, rows):
    path = tmp_path / "disclosure.xlsx"
    pd.DataFrame(rows, columns=HEADERS).to_excel(path, sheet_name="Scheme", index=False)
    return path


@pytest.mark.parametrize("scale", [1.0, 0.01])
def test_exclusions_are_detail_rows_and_nav_is_normalized(tmp_path, scale):
    path = workbook(tmp_path, [
        ["Equities", None, None, None, None],
        ["Example", "INE123A01012", 100, 4, 4 * scale],
        ["Sub Total", None, 100, 4, 4 * scale],
        ["TREPS", None, None, 90, 90 * scale],
        ["Stock future", "BAD-ISIN", 5, 5, 5 * scale],
        ["Cash", None, None, 1, 1 * scale],
        ["Total", None, 105, 100, 100 * scale],
        ["Grand Total", None, 105, 100, 100 * scale],
        ["Net Assets", None, None, 100, 100 * scale],
        ["Additional derivative disclosure", None, None, None, None],
        ["Swap notional (not a NAV weight)", None, None, None, 25000],
        ["Futures contract", "Long", 30, 5000, None],
        HEADERS,
    ])
    parsed = parse_workbook(path, "AMC", "2026-08")
    assert len(parsed) == 1
    assert parsed.iloc[0]["isin"] == "INE123A01012"
    assert parsed.iloc[0]["pct_nav"] == pytest.approx(4)
    assert parsed.iloc[0]["pct_nav_raw"] == pytest.approx(4 * scale)
    assert parsed.iloc[0]["dropped_non_isin_count"] == 3
    assert parsed.iloc[0]["dropped_non_isin_pct_nav"] == pytest.approx(96)
    assert parsed.iloc[0]["pct_nav_scale"] == ("percent" if scale == 1 else "fraction")
    csv = tmp_path / "parsed.csv"
    parsed.to_csv(csv, index=False)
    reread = pd.read_csv(csv)
    assert reread.iloc[0]["dropped_non_isin_pct_nav"] == pytest.approx(96)


def test_unknown_dropped_nav_stays_missing(tmp_path):
    path = workbook(tmp_path, [
        ["Example", "INE123A01012", 100, 4, 4],
        ["TREPS", None, 10, 96, "-"],
        ["Total", None, 110, 100, 100],
    ])
    parsed = parse_workbook(path, "AMC", "2026-08")
    assert parsed.iloc[0]["dropped_non_isin_count"] == 1
    assert pd.isna(parsed.iloc[0]["dropped_non_isin_pct_nav"])
    assert parsed.iloc[0]["pct_nav_scale"] == "unknown"
    assert parsed.iloc[0]["pct_nav"] == 4


def test_all_dropped_scheme_retains_metadata_but_no_holding(tmp_path):
    path = workbook(tmp_path, [
        ["TREPS", None, None, 96, 0.96],
        ["Cash", None, None, 4, 0.04],
        ["Grand Total", None, None, 100, 1.0],
    ])
    parsed = parse_workbook(path, "AMC", "2026-08")
    assert len(parsed) == 1
    row = parsed.iloc[0]
    assert pd.isna(row["isin"])
    assert pd.isna(row["quantity"])
    assert pd.isna(row["pct_nav"])
    assert row["scheme_name"] == "Scheme"
    assert row["dropped_non_isin_count"] == 2
    assert row["dropped_non_isin_pct_nav"] == pytest.approx(100)
    assert row["pct_nav_scale"] == "fraction"


def test_known_zero_and_partial_numeric_nav_are_preserved(tmp_path):
    path = workbook(tmp_path, [
        ["Example", "INE123A01012", 100, 95, 95],
        ["Cash", None, None, 0, 0],
        ["TREPS", None, None, 5, "-"],
    ])
    parsed = parse_workbook(path, "AMC", "2026-08")
    assert parsed.iloc[0]["dropped_non_isin_count"] == 2
    assert parsed.iloc[0]["dropped_non_isin_pct_nav"] == 0
