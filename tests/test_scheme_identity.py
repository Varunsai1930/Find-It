"""Stable scheme identity across sheet renames. Temp DBs only; no network."""

import sqlite3

import pandas as pd
import pytest

import db
from findit.cli.alias import list_schemes, merge_schemes


def _csv(tmp_path, name, scheme_name, month, qty=100.0, value=500.0):
    path = tmp_path / name
    pd.DataFrame([{
        "isin": "INE002A01018", "instrument_name": "Reliance Industries Ltd.",
        "industry": "Refineries", "quantity": qty, "market_value_lakhs": value,
        "pct_nav": 99.0, "scheme_name": scheme_name, "amc_name": "HDFC AMC",
        "report_month": month,
    }]).to_csv(path, index=False)
    return path


# ---- normalization ---------------------------------------------------------

@pytest.mark.parametrize("a, b", [
    ("SBI  Bluechip Fund", "SBI Bluechip Fund"),
    ("HDFC Mid-Cap Fund", "HDFC Mid Cap Fund"),
    ("ICICI Prudential Focused Fund ", "icici prudential focused fund"),
    ("Nifty 50 ETF (Growth)", "Nifty 50 ETF Growth"),
])
def test_formatting_drift_folds_to_one_key(a, b):
    assert db.normalize_scheme_name(a) == db.normalize_scheme_name(b)


@pytest.mark.parametrize("a, b", [
    ("HDFC Top 100 Fund", "HDFC Top 200 Fund"),
    ("SBI Bluechip Fund Direct", "SBI Bluechip Fund Regular"),
    ("SCRF", "SBI Credit Risk Fund"),
])
def test_distinct_schemes_never_collapse(a, b):
    """Never infer a rename: only character drift is absorbed."""
    assert db.normalize_scheme_name(a) != db.normalize_scheme_name(b)


# ---- resolution ------------------------------------------------------------

def test_repunctuated_sheet_reuses_scheme_id(tmp_path):
    conn = db.get_connection(str(tmp_path / "t.db"))
    first = db.resolve_scheme_id(conn, "HDFC AMC", "HDFC Mid-Cap Fund")
    again = db.resolve_scheme_id(conn, "HDFC AMC", "HDFC Mid Cap  Fund")
    assert first == again
    assert conn.execute("SELECT COUNT(*) FROM schemes").fetchone()[0] == 1
    conn.close()


def test_recorded_alias_resolves_to_existing_scheme(tmp_path):
    conn = db.get_connection(str(tmp_path / "t.db"))
    sid = db.resolve_scheme_id(conn, "SBI AMC", "SBI Credit Risk Fund")
    assert db.resolve_scheme_id(conn, "SBI AMC", "SCRF", create=False) is None
    db.record_scheme_alias(conn, sid, "SCRF")
    assert db.resolve_scheme_id(conn, "SBI AMC", "SCRF") == sid
    assert conn.execute("SELECT COUNT(*) FROM schemes").fetchone()[0] == 1
    conn.close()


def test_same_name_different_amcs_stay_separate(tmp_path):
    conn = db.get_connection(str(tmp_path / "t.db"))
    a = db.resolve_scheme_id(conn, "HDFC AMC", "Bluechip Fund")
    b = db.resolve_scheme_id(conn, "SBI AMC", "Bluechip Fund")
    assert a != b
    conn.close()


def test_alias_clashing_within_amc_is_refused(tmp_path):
    conn = db.get_connection(str(tmp_path / "t.db"))
    a = db.resolve_scheme_id(conn, "SBI AMC", "SBI Contra Fund")
    b = db.resolve_scheme_id(conn, "SBI AMC", "SBI Focused Fund")
    with pytest.raises(db.AmbiguousSchemeError):
        db.record_scheme_alias(conn, b, "SBI Contra Fund")
    assert db.resolve_scheme_id(conn, "SBI AMC", "SBI Contra Fund") == a
    conn.close()


def test_ambiguous_key_raises_instead_of_guessing(tmp_path):
    """Two rows already sharing a key must be reported, never silently picked."""
    conn = db.get_connection(str(tmp_path / "t.db"))
    conn.execute("INSERT INTO schemes (amc_name, scheme_name, scheme_key) "
                 "VALUES ('SBI AMC', 'A Fund', 'a fund')")
    conn.execute("INSERT INTO schemes (amc_name, scheme_name, scheme_key) "
                 "VALUES ('SBI AMC', 'A  Fund', 'a fund')")
    conn.commit()
    with pytest.raises(db.AmbiguousSchemeError) as exc:
        db.resolve_scheme_id(conn, "SBI AMC", "a fund")
    assert "merge-from" in str(exc.value)
    conn.close()


# ---- ingestion -------------------------------------------------------------

def test_renamed_sheet_does_not_fork_the_fund(tmp_path):
    """The phantom-exit bug: month 2 under a re-punctuated sheet name."""
    path_db = str(tmp_path / "t.db")
    conn = db.get_connection(path_db)
    db.load_parsed_csv(conn, _csv(tmp_path, "m1.csv", "HDFC Mid-Cap Fund", "2026-07"))
    db.load_parsed_csv(conn, _csv(tmp_path, "m2.csv", "HDFC Mid Cap Fund", "2026-08", qty=150.0))
    assert conn.execute("SELECT COUNT(*) FROM schemes").fetchone()[0] == 1
    months = [r[0] for r in conn.execute(
        "SELECT report_month FROM mf_holdings_monthly ORDER BY report_month")]
    assert months == ["2026-07", "2026-08"]

    import delta_calculator
    deltas = delta_calculator.compute_deltas(conn, "2026-07", "2026-08")
    assert set(deltas["action"]) == {"added"}, "a rename must not read as exit + new"
    conn.close()


def test_backfill_gives_existing_schemes_identity(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "legacy.db"))
    conn.executescript(db.SCHEMA)
    conn.executescript(db.V2_SCHEMA)
    conn.execute("INSERT INTO schemes (amc_name, scheme_name) VALUES ('SBI AMC', 'SBI  Contra')")
    conn.commit()
    db.backfill_scheme_identity(conn)
    assert conn.execute("SELECT scheme_key FROM schemes").fetchone()[0] == "sbi contra"
    assert db.resolve_scheme_id(conn, "SBI AMC", "SBI Contra", create=False) is not None
    conn.close()


# ---- merge -----------------------------------------------------------------

def _forked(tmp_path):
    path_db = str(tmp_path / "t.db")
    conn = db.get_connection(path_db)
    db.load_parsed_csv(conn, _csv(tmp_path, "m1.csv", "OLDNAME", "2026-07"))
    db.load_parsed_csv(conn, _csv(tmp_path, "m2.csv", "HDFC Mid Cap Fund", "2026-08"))
    old = db.resolve_scheme_id(conn, "HDFC AMC", "OLDNAME", create=False)
    new = db.resolve_scheme_id(conn, "HDFC AMC", "HDFC Mid Cap Fund", create=False)
    return conn, old, new


def test_merge_moves_rows_and_keeps_old_name_resolvable(tmp_path):
    conn, old, new = _forked(tmp_path)
    assert old != new
    result = merge_schemes(conn, old, new)
    assert result["moved"]["mf_holdings_monthly"] == 1
    assert conn.execute("SELECT COUNT(*) FROM schemes").fetchone()[0] == 1
    months = [r[0] for r in conn.execute(
        "SELECT report_month FROM mf_holdings_monthly WHERE scheme_id = ? "
        "ORDER BY report_month", (new,))]
    assert months == ["2026-07", "2026-08"]
    # A later file still carrying the old sheet name lands on the survivor.
    assert db.resolve_scheme_id(conn, "HDFC AMC", "OLDNAME") == new
    conn.close()


def test_merge_refuses_conflicting_rows(tmp_path):
    conn, old, new = _forked(tmp_path)
    db.load_parsed_csv(conn, _csv(tmp_path, "m3.csv", "OLDNAME", "2026-08"))
    with pytest.raises(ValueError) as exc:
        merge_schemes(conn, old, new)
    assert "refusing to merge" in str(exc.value)
    assert conn.execute("SELECT COUNT(*) FROM schemes").fetchone()[0] == 2, "no partial merge"
    conn.close()


def test_merge_refuses_across_amcs(tmp_path):
    conn = db.get_connection(str(tmp_path / "t.db"))
    a = db.resolve_scheme_id(conn, "HDFC AMC", "Fund One")
    b = db.resolve_scheme_id(conn, "SBI AMC", "Fund Two")
    with pytest.raises(ValueError, match="across AMCs"):
        merge_schemes(conn, a, b)
    conn.close()


def test_list_schemes_reports_aliases_and_months(tmp_path):
    conn, old, new = _forked(tmp_path)
    merge_schemes(conn, old, new)
    rows = list_schemes(conn)
    assert len(rows) == 1
    assert "OLDNAME" in rows[0]["aliases"]
    assert rows[0]["months"] == ["2026-07", "2026-08"]
    conn.close()
