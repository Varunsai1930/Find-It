"""Signal evaluation. Synthetic panels only; no network, no real DB."""

import pandas as pd
import pytest

from findit.cli import backtest


def _consensus(rows):
    return pd.DataFrame(rows)


def _px(pairs, col="close_price"):
    return pd.DataFrame([{"isin": i, col: p, "trade_date": "2026-08-31"} for i, p in pairs])


@pytest.mark.parametrize("net, common, expected", [
    (2, True, "mf_and_fii"),
    (2, False, "mf_only"),
    (0, False, "mf_neutral"),
    (-1, False, "mf_selling"),
    (0, True, "mf_neutral"),
])
def test_grouping(net, common, expected):
    row = {"net_amc_count": net, "is_common_with_fii_increase": common}
    assert backtest.classify(row) == expected


def test_forward_return_is_computed_from_the_two_closes():
    panel = backtest.build_panel(
        _consensus([{"isin": "A", "net_amc_count": 2, "is_common_with_fii_increase": True}]),
        _px([("A", 100.0)]), _px([("A", 110.0)]))
    assert panel.loc[0, "forward_return"] == pytest.approx(0.10)
    assert panel.loc[0, "group"] == "mf_and_fii"


def test_unpriced_names_are_dropped_never_treated_as_flat():
    panel = backtest.build_panel(
        _consensus([{"isin": "A", "net_amc_count": 1, "is_common_with_fii_increase": False},
                    {"isin": "MISSING", "net_amc_count": 1,
                     "is_common_with_fii_increase": False}]),
        _px([("A", 100.0)]), _px([("A", 110.0)]))
    assert list(panel["isin"]) == ["A"], "a 0% return would be an invented observation"


def test_zero_or_negative_prices_are_excluded():
    panel = backtest.build_panel(
        _consensus([{"isin": "A", "net_amc_count": 1, "is_common_with_fii_increase": False}]),
        _px([("A", 0.0)]), _px([("A", 110.0)]))
    assert panel.empty


def test_excess_is_measured_against_the_tracked_universe():
    rows, sig, fwd = [], [], []
    for i in range(10):  # winners carry the signal, losers do not
        isin, win = f"W{i}", True
        rows.append({"isin": isin, "net_amc_count": 2, "is_common_with_fii_increase": win})
        sig.append((isin, 100.0))
        fwd.append((isin, 110.0))
    for i in range(10):
        isin = f"L{i}"
        rows.append({"isin": isin, "net_amc_count": -1, "is_common_with_fii_increase": False})
        sig.append((isin, 100.0))
        fwd.append((isin, 90.0))
    result = backtest.evaluate(
        backtest.build_panel(_consensus(rows), _px(sig), _px(fwd)), iterations=500)
    assert result["universe"]["n"] == 20
    assert result["universe"]["mean"] == pytest.approx(0.0)
    assert result["groups"]["mf_and_fii"]["mean"] == pytest.approx(0.10)
    assert result["groups"]["mf_and_fii"]["excess_mean"] == pytest.approx(0.10)
    assert result["groups"]["mf_and_fii"]["win_rate"] == 1.0
    assert result["groups"]["mf_selling"]["excess_mean"] == pytest.approx(-0.10)


def test_a_signal_that_does_nothing_reads_as_no_edge():
    rows, sig, fwd = [], [], []
    for i in range(20):
        isin = f"S{i}"
        rows.append({"isin": isin, "net_amc_count": 2 if i % 2 else -1,
                     "is_common_with_fii_increase": bool(i % 2)})
        sig.append((isin, 100.0))
        fwd.append((isin, 110.0 if i % 4 < 2 else 90.0))
    result = backtest.evaluate(
        backtest.build_panel(_consensus(rows), _px(sig), _px(fwd)), iterations=500)
    assert abs(result["groups"]["mf_and_fii"]["excess_mean"]) < 1e-9
    assert result["groups"]["mf_and_fii"]["p_vs_random"] > 0.2


def test_empty_inputs_do_not_fabricate_a_result():
    assert backtest.evaluate(pd.DataFrame(columns=["forward_return"]))["universe"]["n"] == 0
    assert backtest.build_panel(pd.DataFrame(), _px([("A", 1.0)]), _px([("A", 1.0)])).empty


def test_report_always_carries_the_sample_size_caveat():
    rows = [{"isin": "A", "net_amc_count": 2, "is_common_with_fii_increase": True}]
    panel = backtest.build_panel(_consensus(rows), _px([("A", 100.0)]), _px([("A", 110.0)]))
    result = backtest.evaluate(panel, iterations=100)
    result.update(signal_month="2026-08", forward_month="2026-09",
                  price_dates=("2026-08-31", "2026-09-18"),
                  consensus_rows=1, unpriced=0)
    text = backtest.report(result)
    assert "One signal month is one event, not a sample" in text
    assert "not a strategy" in text
