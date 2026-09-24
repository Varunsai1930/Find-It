"""Publication-dated entry, multi-period track record, quarterly history test.

Synthetic DBs under tmp_path; no network.
"""

from datetime import date

import pandas as pd
import pytest

import db
from findit.cli import backtest, backtest_quarterly as bq


# ---- monthly ----------------------------------------------------------------

def test_holding_period_starts_after_publication_and_ends_at_the_next_entry():
    period = backtest.holding_period("2026-07", today=date(2026, 9, 23))
    assert period["entry_target"] == date(2026, 8, 11)
    assert period["exit_target"] == date(2026, 9, 11)
    assert period["exit_in_future"] is False
    assert backtest.holding_period("2026-12", today=date(2027, 1, 5))["entry_in_future"]


@pytest.mark.parametrize("net_active, net_raw, common, expected", [
    (2, 1, True, "active_and_fii"),
    (2, 1, False, "active_only"),
    (1, 0, True, "active_only"),  # FII agreement needs raw MF buying too
    (0, 3, True, "active_neutral"),
    (-1, 2, False, "active_selling"),
])
def test_active_grouping(net_active, net_raw, common, expected):
    row = {"net_active_amc_count": net_active, "net_amc_count": net_raw,
           "is_common_with_fii_increase": common}
    assert backtest.classify_active(row) == expected


def _result(month, excess, partial=False):
    return {"signal_month": month, "price_dates": ("a", "b"), "partial_period": partial,
            "groups": {"mf_only": {"n": 5, "excess_mean": excess}}}


def test_pooled_t_stat_is_across_months_and_skips_partial_periods():
    results = [_result("2026-01", 0.01), _result("2026-02", 0.03),
               _result("2026-03", 0.02), _result("2026-04", 0.50, partial=True)]
    stats = backtest.pooled(results, ("mf_only",))["mf_only"]
    assert stats["months"] == 3
    assert stats["mean_excess"] == pytest.approx(0.02)
    assert stats["t_stat"] == pytest.approx(0.02 / (0.01 / 3 ** 0.5))
    assert stats["months_beating_universe"] == 3


def test_track_record_marks_partial_periods_and_keeps_the_caveat():
    text = backtest.track_record([_result("2026-08", 0.01, partial=True)], ("mf_only",))
    assert "2026-08" in text and "*" in text
    assert "0 complete signal month(s)" in text


def test_month_range():
    assert backtest.month_range("2025-11", "2026-02") == [
        "2025-11", "2025-12", "2026-01", "2026-02"]


def test_missing_entry_prices_name_the_command_to_run(tmp_path):
    path = str(tmp_path / "t.db")
    db.get_connection(path).close()
    with pytest.raises(SystemExit) as exc:
        backtest.run(path, "2026-07")
    assert "--date 2026-08-11" in str(exc.value)


# ---- quarterly --------------------------------------------------------------

def _filings(rows):
    frame = pd.DataFrame(rows, columns=["isin", "quarter_end", "mf_pct", "fii_pct",
                                        "published_at"])
    published, basis = zip(*(bq.publication.shareholding_published(q, p)
                              for q, p in zip(frame["quarter_end"], frame["published_at"])))
    frame["published"], frame["basis"] = list(published), list(basis)
    return frame


def test_quarter_neighbours():
    assert bq.previous_quarter_end("2026-03-31") == "2025-12-31"
    assert bq.next_quarter_end("2025-12-31") == "2026-03-31"


def test_late_filer_is_left_out_not_back_filled():
    filings = _filings([
        ("ONTIME", "2019-12-31", 1.0, 5.0, "2020-01-20T18:00"),
        ("ONTIME", "2020-03-31", 2.0, 6.0, "2020-04-20T18:00"),
        ("LATE", "2019-12-31", 1.0, 5.0, "2020-01-20T18:00"),
        ("LATE", "2020-03-31", 3.0, 5.0, "2020-05-14T18:00"),  # COVID extension
    ])
    panel, audit = bq.signal_panel(filings, "2020-03-31", bq.decision_date("2020-03-31"))
    assert list(panel["isin"]) == ["ONTIME"]
    assert audit["late_excluded"] == 1
    assert panel.iloc[0]["group"] == "mf_up_fii_up"


def test_deadline_basis_is_counted_and_can_be_required_away():
    filings = _filings([
        ("A", "2025-12-31", 1.0, 1.0, None),
        ("A", "2026-03-31", 1.0, 1.0, None),
    ])
    decision = bq.decision_date("2026-03-31")
    panel, audit = bq.signal_panel(filings, "2026-03-31", decision)
    assert audit["deadline_basis"] == 1 and len(panel) == 1
    strict, _ = bq.signal_panel(filings, "2026-03-31", decision, require_observed=True)
    assert strict.empty


def test_score_quarter_groups_and_excess():
    panel = pd.DataFrame({"isin": list("ABCDE"),
                          "d_mf": [2.0, 1.0, 0.0, -0.1, -2.0],
                          "d_fii": [1.0, 0.0, 0.0, 0.0, 0.0],
                          "group": ["mf_up_fii_up", "mf_up_only", "mf_flat", "mf_flat",
                                    "mf_down"]})
    entry = pd.DataFrame({"isin": list("ABCDE"), "close_price": [100.0] * 5})
    exit_ = pd.DataFrame({"isin": list("ABCDE"), "close_price": [120, 110, 100, 100, 70.0]})
    scored = bq.score_quarter(panel, entry, exit_)
    assert scored["universe"]["mean"] == pytest.approx(0.0)
    assert scored["groups"]["mf_up_fii_up"]["excess_mean"] == pytest.approx(0.20)
    assert scored["groups"]["mf_down"]["excess_mean"] == pytest.approx(-0.30)
    assert scored["groups"]["mf_top_q"]["n"] == 1
    assert scored["groups"]["mf_top_q"]["excess_mean"] == pytest.approx(0.20)


def test_quarterly_run_end_to_end(tmp_path):
    path = str(tmp_path / "t.db")
    conn = db.get_connection(path)
    records = []
    for i in range(10):
        up = i < 5
        records += [
            {"isin": f"INE{i:03d}A01001", "quarter_end": "2025-09-30", "mf_pct": 5.0,
             "fii_pct": 10.0, "published_at": "2025-10-15T18:00"},
            {"isin": f"INE{i:03d}A01001", "quarter_end": "2025-12-31",
             "mf_pct": 6.0 if up else 4.0, "fii_pct": 10.0, "published_at": "2026-01-15T18:00"},
        ]
    frame = pd.DataFrame(records).assign(promoter_pct=50.0, dii_pct=10.0, public_pct=50.0,
                                         source="test", filing_type="quarterly")
    db.load_shareholding_records(conn, frame)
    # Entry: first trading day after Jan 30 -> Jan 31; exit after Apr 30 -> May 1.
    for day, winners, losers in [("2026-01-31", 100.0, 100.0), ("2026-05-01", 110.0, 95.0)]:
        conn.executemany(
            "INSERT INTO security_prices_daily (isin, trade_date, close_price) VALUES (?, ?, ?)",
            [(f"INE{i:03d}A01001", day, winners if i < 5 else losers) for i in range(10)])
    conn.commit()
    conn.close()
    outcome = bq.run(path, ["2025-12-31"])
    assert outcome["missing_dates"] == []
    (result,) = outcome["results"]
    assert result["price_dates"] == ("2026-01-31", "2026-05-01")
    assert result["groups"]["mf_up_only"]["excess_mean"] == pytest.approx(0.075)
    assert result["groups"]["mf_down"]["excess_mean"] == pytest.approx(-0.075)
    text = bq.report(outcome, path, bq.DEFAULT_LAG_DAYS, bq.DEFAULT_THRESHOLD_PP)
    assert "survivorship" in text


def test_quarterly_run_lists_missing_price_dates(tmp_path):
    path = str(tmp_path / "t.db")
    conn = db.get_connection(path)
    db.load_shareholding_records(conn, pd.DataFrame([
        {"isin": "INE000A01001", "quarter_end": q, "mf_pct": 1.0, "fii_pct": 1.0,
         "published_at": p, "promoter_pct": 50.0, "dii_pct": 1.0, "public_pct": 50.0,
         "source": "test"}
        for q, p in [("2025-09-30", "2025-10-10"), ("2025-12-31", "2026-01-10")]]))
    conn.close()
    outcome = bq.run(path, ["2025-12-31"])
    assert outcome["missing_dates"] == [date(2026, 1, 31), date(2026, 5, 1)]
    assert "--date 2026-01-31 --date 2026-05-01" in bq.report(outcome, path, 30, 0.25)


def test_track_record_lists_months_it_could_not_score(tmp_path):
    path = str(tmp_path / "t.db")
    conn = db.get_connection(path)
    conn.execute("INSERT INTO mf_holding_deltas (scheme_id, isin, report_month, prev_month, "
                 "action) VALUES (1, 'INEAAA01001', '2026-07', '2026-06', 'added')")
    conn.commit()
    text = backtest.db_track_record(path)
    assert "no signal month has entry/exit closes stored yet" in text
    assert "2026-07: no stored close" in text
    assert "--date 2026-08-11" in text
    conn.close()


def test_too_few_stocks_leave_the_fifths_empty_without_failing():
    panel = pd.DataFrame({"isin": ["A", "B"], "d_mf": [1.0, -1.0], "d_fii": [0.0, 0.0],
                          "group": ["mf_up_only", "mf_down"]})
    prices = pd.DataFrame({"isin": ["A", "B"], "close_price": [100.0, 100.0]})
    scored = bq.score_quarter(panel, prices, prices)
    assert scored["groups"]["mf_top_q"] == {"n": 0}
    assert scored["groups"]["mf_up_only"]["n"] == 1


def test_size_neutral_view_removes_a_pure_size_effect():
    # 30 stocks: the ten smallest (by turnover) all rose 10%, the rest were flat,
    # and MF buying happened to be concentrated in those small stocks. Raw excess
    # makes "mf_up_only" look good; against same-size peers it is zero.
    isins = [f"S{i:02d}" for i in range(30)]
    panel = pd.DataFrame({"isin": isins, "d_mf": [1.0] * 10 + [0.0] * 20,
                          "d_fii": [0.0] * 30,
                          "group": ["mf_up_only"] * 10 + ["mf_flat"] * 20})
    entry = pd.DataFrame({"isin": isins, "close_price": [100.0] * 30,
                          "traded_value": [1e5 * (i + 1) for i in range(30)]})
    exit_ = pd.DataFrame({"isin": isins, "close_price": [110.0] * 10 + [100.0] * 20})
    scored = bq.score_quarter(panel, entry, exit_)
    up = scored["groups"]["mf_up_only"]
    assert up["excess_mean"] == pytest.approx(0.10 - 1 / 30)
    assert up["excess_size"] == pytest.approx(0.0)
    assert bq.size_neutral([dict(scored, signal_quarter="q", price_dates=("a", "b"))])[0][
        "groups"]["mf_up_only"] == {"n": 10, "excess_mean": pytest.approx(0.0)}


def test_without_turnover_there_is_no_size_neutral_figure():
    panel = pd.DataFrame({"isin": ["A", "B"], "d_mf": [1.0, 0.0], "d_fii": [0.0, 0.0],
                          "group": ["mf_up_only", "mf_flat"]})
    prices = pd.DataFrame({"isin": ["A", "B"], "close_price": [100.0, 100.0]})
    assert "excess_size" not in bq.score_quarter(panel, prices, prices)["groups"]["mf_up_only"]
