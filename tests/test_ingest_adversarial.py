"""Adversarial parser regressions; synthetic workbooks only."""

from __future__ import annotations

import json

import openpyxl
import pytest

from findit.ingest import amfi_mf_parser


HEADER = ["Name of Instrument", "ISIN", "Quantity", "Market Value", "% to NAV"]
ISIN = "INE040A01034"
NOTE_ISIN = "INE009A01021"


def _workbook(tmp_path, rows):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(HEADER)
    for row in rows:
        sheet.append(row)
    path = tmp_path / "adversarial.xlsx"
    workbook.save(path)
    return path


@pytest.mark.parametrize("label", ["Total Net Assets", "Net Assets", "Net Asset Value"])
def test_final_nav_total_ends_holdings_before_isin_notes(tmp_path, label):
    path = _workbook(tmp_path, [
        ["Alpha", ISIN, 100, 60, 60],
        ["Cash", None, None, 40, 40],
        [label, None, None, 100, 100],
        ["Exposure detail", NOTE_ISIN, 900, 90, 90],
    ])

    rows = amfi_mf_parser.parse_workbook(path, "Test AMC", "2026-08")

    assert rows["isin"].tolist() == [ISIN]
    assert rows["quantity"].tolist() == [100]
    assert rows["source_row"].tolist() == [2]
    assert rows["dropped_non_isin_pct_nav"].tolist() == [40]


@pytest.mark.parametrize("label", ["Total Net Assets", "Net Assets", "Net Asset Value"])
def test_summary_nav_total_above_holdings_does_not_end_them(tmp_path, label):
    path = _workbook(tmp_path, [
        [label, None, None, 100, 100],
        ["Alpha", ISIN, 100, 100, 100],
        [label, None, None, 100, 100],
        ["Exposure detail", NOTE_ISIN, 900, 90, 90],
    ])

    rows = amfi_mf_parser.parse_workbook(path, "Test AMC", "2026-08")

    assert rows["isin"].tolist() == [ISIN]
    assert rows["source_row"].tolist() == [3]


@pytest.mark.parametrize("label", [
    "Grand Total (AUM)", "Total Net Assets", "Net Assets", "Net Asset Value",
])
def test_summary_total_does_not_hide_omitted_holdings(tmp_path, label):
    path = _workbook(tmp_path, [
        [label, None, None, 100, 100],
        ["Alpha", ISIN, 100, 60, 60],
        ["Cash", None, None, 40, 40],
        [label, None, None, 100, 100],
        ["Contract note", None, 900, 90, 90],
    ])

    rows = amfi_mf_parser.parse_workbook(path, "Test AMC", "2026-08")

    assert rows["isin"].tolist() == [ISIN]
    assert rows["dropped_non_isin_count"].tolist() == [1]
    assert rows["dropped_non_isin_pct_nav"].tolist() == [40]


@pytest.mark.parametrize("column", ["quantity", "market_value_lakhs", "pct_nav"])
@pytest.mark.parametrize("invalid", ["inf", "-inf", "Infinity", "-Infinity"])
def test_nonfinite_holding_numbers_are_missing_and_reported(
    tmp_path, capsys, column, invalid,
):
    holding = ["Alpha", ISIN, 100, 60, 60]
    holding[{"quantity": 2, "market_value_lakhs": 3, "pct_nav": 4}[column]] = invalid
    path = _workbook(tmp_path, [holding, ["Total Net Assets", None, None, 100, 100]])

    rows = amfi_mf_parser.parse_workbook(path, "Test AMC", "2026-08")

    assert rows[column].isna().all()
    assert rows["isin"].tolist() == [ISIN]
    raw = json.loads(rows.iloc[0]["source_raw_json"])
    assert raw[column] == invalid
    assert "numeric value that didn't parse" in capsys.readouterr().err
