"""Core pure-function tests. Synthetic data only; no DB/IO, no network."""

import pytest

from findit.core.corporate_actions import detect_candidate
from findit.core.instruments import classify_isin


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
