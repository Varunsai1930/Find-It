"""Core pure-function tests. Synthetic data only; no DB/IO, no network."""

import pandas as pd
import pytest

from findit.core.corporate_actions import adjust_quantity, detect_candidate
from findit.core.flows import decompose
from findit.core.instruments import classify_isin
from findit.core.units import normalize_nav
from findit.core.validation import (
    check_duplicates,
    validate_scheme_month,
    validate_shareholding,
)


# ---- instruments -----------------------------------------------------------


def test_bse_equity():
    assert classify_isin("INE118H01025") == "equity"


def test_hdfc_bank_cd():
    assert classify_isin("INE040A16HO2") == "cp_or_cd"


def test_preference():
    assert classify_isin("INE000002XXX9") == "preference"


@pytest.mark.parametrize("code", ["07", "08", "09", "10", "11", "12"])
def test_ncd_codes(code):
    assert classify_isin(f"INE0000{code}XXX9") == "ncd"


@pytest.mark.parametrize("code", ["14", "16"])
def test_cp_cd_codes(code):
    assert classify_isin(f"INE0000{code}XXX9") == "cp_or_cd"


def test_debt_other_unmapped_e_subtype():
    assert classify_isin("INE000003XXX9") == "debt_other"


def test_debt_other_short_e_isin():
    assert classify_isin("INE12") == "debt_other"


def test_tbill_or_gsec():
    assert classify_isin("IN00202104567") == "tbill_or_gsec"


def test_sgsec():
    assert classify_isin("IN9220150070") == "sgsec"


def test_mf_units():
    assert classify_isin("INF209K01ABC0") == "mf_units"


def test_foreign():
    assert classify_isin("US0378331005") == "foreign"


def test_other_third_char():
    assert classify_isin("INA1234567890") == "other"


def test_normalize_case_and_strip():
    assert classify_isin("  ine118h01025  ") == "equity"


def test_non_str_and_short_safe():
    assert classify_isin(None) == "other"
    assert classify_isin(123) == "other"
    assert classify_isin("IN") == "other"
    # Must not raise; empty/garbage without IN prefix -> foreign per rule.
    assert classify_isin("") == "foreign"


# ---- units -----------------------------------------------------------------


def test_nav_fraction_inference():
    vals, scale, warnings = normalize_nav([0.0066, 0.9934])
    assert scale == "fraction"
    assert warnings == []
    assert vals[0] == pytest.approx(0.66)
    assert vals[1] == pytest.approx(99.34)


def test_nav_single_fraction_via_explicit_scale():
    vals, scale, _ = normalize_nav([0.0066], scale="fraction")
    assert scale == "fraction"
    assert vals == pytest.approx([0.66])


def test_nav_percent_passthrough():
    vals, scale, _ = normalize_nav([0.66, 99.34])
    assert scale == "percent"
    assert vals == pytest.approx([0.66, 99.34])


def test_nav_ambiguous_sum_raises():
    with pytest.raises(ValueError):
        normalize_nav([10.0, 10.0])  # sum=20: neither fraction nor percent
    with pytest.raises(ValueError):
        normalize_nav([0.0066])  # sum far from 1.0: never silently guess
    with pytest.raises(ValueError):
        normalize_nav([50.0])  # sum=50: outside both bands


def test_nav_explicit_scale_overrides_inference():
    vals, scale, _ = normalize_nav([10.0, 10.0], scale="percent")
    assert scale == "percent"
    assert vals == pytest.approx([10.0, 10.0])
    vals, scale, _ = normalize_nav([0.1, 0.1], scale="fraction")
    assert scale == "fraction"
    assert vals == pytest.approx([10.0, 10.0])
    with pytest.raises(ValueError):
        normalize_nav([50.0, 50.0], scale="bogus")


def test_nav_does_not_mutate_input():
    raw = [0.5, 0.5]
    snapshot = list(raw)
    normalize_nav(raw)
    assert raw == snapshot


# ---- flows -----------------------------------------------------------------


def test_flow_sign_flip_buy_shares_value_down():
    out = decompose(100, 10.0, 150, 9.0)
    # flow = 50 * (9/150) = 3.0 > 0 even though value fell by 1.0
    assert out["flow_lakhs"] == pytest.approx(3.0)
    assert out["value_change_lakhs"] == pytest.approx(-1.0)
    assert out["price_effect_lakhs"] == pytest.approx(-4.0)
    assert out["flow_lakhs"] > 0
    assert out["value_change_lakhs"] < 0


def test_flow_exit_convention():
    out = decompose(100, 10.0, 0, 0.0)
    assert out["flow_lakhs"] == pytest.approx(-10.0)
    assert out["price_effect_lakhs"] == pytest.approx(0.0)
    assert out["value_change_lakhs"] == pytest.approx(-10.0)


def test_flow_new_position():
    out = decompose(0, 0.0, 100, 5.0)
    assert out["flow_lakhs"] == pytest.approx(5.0)
    assert out["value_change_lakhs"] == pytest.approx(5.0)
    assert out["price_effect_lakhs"] == pytest.approx(0.0)


def test_flow_identity_value_change_minus_flow():
    out = decompose(100, 10.0, 120, 13.2)
    assert out["value_change_lakhs"] == pytest.approx(3.2)
    assert out["flow_lakhs"] + out["price_effect_lakhs"] == pytest.approx(
        out["value_change_lakhs"]
    )


# ---- corporate actions -----------------------------------------------------


def test_split_candidate_2x():
    cand = detect_candidate(100, 200.0, 200, 100.0)
    assert cand is not None
    assert cand["qty_ratio"] == pytest.approx(2.0)
    assert cand["price_ratio"] == pytest.approx(0.5)


def test_reverse_split_candidate_half():
    cand = detect_candidate(200, 50.0, 100, 100.0)
    assert cand is not None
    assert cand["qty_ratio"] == pytest.approx(0.5)
    assert cand["price_ratio"] == pytest.approx(2.0)


def test_split_within_tolerance():
    cand = detect_candidate(100, 100.0, 202, 49.0)
    assert cand is not None  # ~2.02x qty, ~0.49x px: both within 5%


def test_no_candidate_when_price_not_inverse():
    assert detect_candidate(100, 100.0, 200, 80.0) is None
    assert detect_candidate(100, 100.0, 110, 90.0) is None


def test_no_candidate_on_bad_inputs():
    assert detect_candidate(0, 100.0, 200, 50.0) is None
    assert detect_candidate(100, 0, 200, 50.0) is None
    assert detect_candidate(-100, 10.0, 200, 5.0) is None


def test_adjust_quantity():
    assert adjust_quantity(100, 2.0) == pytest.approx(200.0)
    assert adjust_quantity(200, 0.5) == pytest.approx(100.0)


# ---- validation ------------------------------------------------------------


def test_scheme_month_sum_warn_partial_sheet():
    df = pd.DataFrame({"isin": ["A", "B"], "pct_nav": [10.0, 10.0]})
    issues = validate_scheme_month(df)
    assert any("95-105" in i for i in issues)


def test_scheme_month_ok_sum_no_warn():
    df = pd.DataFrame({"isin": ["A", "B"], "pct_nav": [50.0, 50.0]})
    issues = validate_scheme_month(df)
    assert not any("95-105" in i for i in issues)
    assert issues == []


def test_scheme_month_single_line_range_flag():
    df = pd.DataFrame({"isin": ["A", "B", "C"], "pct_nav": [60.0, 70.0, 10.0]})
    issues = validate_scheme_month(df)
    flags = [i for i in issues if "<0 or >50" in i]
    assert len(flags) == 1  # single-line flag even for multiple bad rows


def test_scheme_month_negative_flag():
    df = pd.DataFrame({"isin": ["A", "B"], "pct_nav": [101.0, -1.0]})
    issues = validate_scheme_month(df)
    assert any("<0 or >50" in i for i in issues)


def test_duplicates_helper():
    df = pd.DataFrame({"isin": ["X", "X", "Y"], "pct_nav": [40.0, 40.0, 20.0]})
    dup = check_duplicates(df)
    assert len(dup) == 1
    assert "X" in dup[0]
    issues = validate_scheme_month(df)
    assert any("duplicates" in i for i in issues)


def test_shareholding_ok_and_breach():
    assert validate_shareholding(50, 49) == []
    assert validate_shareholding(70, 30) == []
    bad = validate_shareholding(60, 30)
    assert len(bad) == 1
