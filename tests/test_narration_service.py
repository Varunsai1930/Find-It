import json
import sqlite3

import pytest

import db
from findit.cli.narrate import main
from findit.narrate.service import generate_batch, get_summary

MONTH = "2026-08"


@pytest.fixture
def seeded(tmp_path):
    path = tmp_path / "narration.db"
    conn = db.get_connection(str(path))
    conn.execute("INSERT INTO schemes (scheme_id, amc_name, scheme_name) "
                 "VALUES (1, 'AMC', 'Equity Fund')")
    conn.execute("INSERT INTO stocks (isin, name, instrument_type) "
                 "VALUES ('INE002A01018', 'Example Ltd', 'equity')")
    for month, qty, value in [("2026-07", 100, 100), (MONTH, 150, 150)]:
        conn.execute("INSERT INTO mf_holdings_monthly "
                     "(scheme_id, isin, report_month, quantity, market_value_lakhs, pct_nav) "
                     "VALUES (1, 'INE002A01018', ?, ?, ?, 100)", (month, qty, value))
        conn.execute("INSERT INTO scheme_month_status "
                     "(scheme_id, report_month, status) VALUES (1, ?, 'ok')", (month,))
    conn.execute("INSERT INTO mf_holding_deltas "
                 "(scheme_id, isin, report_month, prev_month, qty_change, "
                 "value_change_lakhs, flow_lakhs, price_effect_lakhs, pct_nav_change, action) "
                 "VALUES (1, 'INE002A01018', ?, '2026-07', 50, 50, 50, 0, 0, 'added')", (MONTH,))
    conn.commit()
    yield conn, path
    conn.close()


class Provider:
    model_version = "zai:glm-5.3:editorial-v1"

    def __init__(self, callback=None):
        self.calls = 0
        self.callback = callback

    def generate(self, prompt):
        self.calls += 1
        if self.callback:
            self.callback()
        return {"sentences": [{"fact_id": fact["id"], "variant": 0}
                              for fact in prompt["facts"]]}


def test_rules_idempotence_and_stale_hash(seeded):
    conn, _ = seeded
    assert generate_batch(conn, MONTH)[0]["status"] == "generated"
    first = get_summary(conn, 1, MONTH)
    assert first["cached"] and first["generated_by"] == "rules"
    assert generate_batch(conn, MONTH)[0]["status"] == "cached"
    conn.execute("UPDATE schemes SET scheme_name='Renamed Equity Fund' WHERE scheme_id=1")
    conn.commit()
    stale = get_summary(conn, 1, MONTH)
    assert not stale["cached"] and "Renamed Equity Fund" in stale["text"]
    assert stale["source_data_hash"] != first["source_data_hash"]
    assert generate_batch(conn, MONTH)[0]["status"] == "generated"


def test_ai_cache_model_change_and_force(seeded):
    conn, _ = seeded
    provider = Provider()
    assert generate_batch(conn, MONTH, provider=provider)[0]["status"] == "generated"
    assert get_summary(conn, 1, MONTH)["generated_by"] == "zai"
    assert generate_batch(conn, MONTH, provider=provider)[0]["status"] == "cached"
    assert provider.calls == 1
    provider.model_version = "zai:glm-5.4:editorial-v1"
    assert generate_batch(conn, MONTH, provider=provider)[0]["status"] == "generated"
    assert generate_batch(conn, MONTH, provider=provider, force=True)[0]["status"] == "generated"
    assert provider.calls == 3
    # Switching explicitly to offline rules regenerates that desired policy.
    assert generate_batch(conn, MONTH)[0]["generated_by"] == "rules"


@pytest.mark.parametrize("bad_plan", [False, True])
def test_failure_fallback_is_safe_and_later_ai_retry_works(seeded, bad_plan):
    conn, _ = seeded

    class Broken(Provider):
        def generate(self, prompt):
            if bad_plan:
                return {"text": "Invented holdings and returns"}
            raise RuntimeError("secret-api-key-DO-NOT-EXPOSE")

    report = generate_batch(conn, MONTH, provider=Broken())[0]
    assert report["status"] == "fallback" and report["generated_by"] == "rules"
    assert "secret-api-key" not in json.dumps(report)
    assert get_summary(conn, 1, MONTH)["generated_by"] == "rules"
    provider = Provider()
    assert generate_batch(conn, MONTH, provider=provider)[0]["status"] == "generated"
    assert provider.calls == 1


@pytest.mark.parametrize("quarantine", [True, False])
def test_ineligible_never_returns_cached_or_calls_provider(seeded, quarantine):
    conn, _ = seeded
    generate_batch(conn, MONTH)
    if quarantine:
        conn.execute("UPDATE scheme_month_status SET status='quarantined' "
                     "WHERE report_month='2026-07'")
    else:
        conn.execute("DELETE FROM mf_holding_deltas")
    conn.commit()
    summary = get_summary(conn, 1, MONTH)
    assert not summary["cached"] and not summary["eligible"]
    assert summary["reason"] == ("quarantined" if quarantine else "no_data")
    provider = Provider()
    assert generate_batch(conn, MONTH, provider=provider)[0]["status"] == "skipped"
    assert provider.calls == 0


def test_source_change_during_provider_call_is_not_saved(seeded):
    conn, path = seeded

    def mutate_source():
        assert not conn.in_transaction
        with sqlite3.connect(path) as other:
            other.execute("UPDATE schemes SET scheme_name='Changed during generation'")

    report = generate_batch(conn, MONTH, provider=Provider(mutate_source))[0]
    assert report["status"] == "skipped" and report["reason"] == "source_changed"
    assert conn.execute("SELECT COUNT(*) FROM fund_summaries").fetchone()[0] == 0


def test_get_summary_is_readonly_with_missing_cache_table(seeded):
    conn, path = seeded
    conn.execute("DROP TABLE fund_summaries")
    conn.commit()
    before = path.read_bytes()
    ro = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        summary = get_summary(ro, 1, MONTH)
        assert not summary["cached"] and summary["eligible"]
    finally:
        ro.close()
    assert path.read_bytes() == before


@pytest.mark.parametrize("origin,version", [("unknown", "rules-v1"), ("rules", "rules-v0"),
                                            ("zai", "zai:glm-5.3:editorial-v0")])
def test_unrecognized_cached_generation_is_ignored(seeded, origin, version):
    conn, _ = seeded
    generate_batch(conn, MONTH)
    conn.execute("UPDATE fund_summaries SET generated_by=?, model_version=?", (origin, version))
    conn.commit()
    assert not get_summary(conn, 1, MONTH)["cached"]


def test_cli_dryrun_never_constructs_provider_or_writes(seeded, monkeypatch, capsys):
    conn, path = seeded
    conn.execute("DROP TABLE fund_summaries")
    conn.commit()
    before = path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("dry-run constructed provider")

    monkeypatch.setattr("findit.narrate.provider.ZaiNarrator", forbidden)
    assert main(["--db", str(path), "--month", MONTH, "--provider", "zai", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "eligible"
    assert path.read_bytes() == before


def test_cli_rules_writes_only_summaries(seeded, capsys):
    conn, path = seeded
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
              if r[0] != "fund_summaries"]
    before = {t: conn.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables}
    assert main(["--db", str(path), "--month", MONTH]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "generated"
    assert {t: conn.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables} == before


def test_cli_rejects_missing_db_and_invalid_month(seeded, tmp_path):
    _, path = seeded
    missing = tmp_path / "missing.db"
    with pytest.raises(SystemExit):
        main(["--db", str(missing), "--month", MONTH])
    assert not missing.exists()
    with pytest.raises(SystemExit):
        main(["--db", str(path), "--month", "2026-13"])


def test_scheme_order_and_argument_validation(seeded):
    conn, _ = seeded
    assert len(generate_batch(conn, MONTH, [1, 1])) == 1
    for ids in [[0], [True], ["1"]]:
        with pytest.raises(ValueError):
            generate_batch(conn, MONTH, ids)
    conn.execute("UPDATE schemes SET scheme_name=scheme_name")
    with pytest.raises(ValueError, match="existing transaction"):
        generate_batch(conn, MONTH)
    conn.rollback()


def test_default_batch_includes_fully_exited_schemes(seeded):
    conn, _ = seeded
    conn.execute("DELETE FROM mf_holdings_monthly WHERE report_month=?", (MONTH,))
    conn.execute("UPDATE mf_holding_deltas SET qty_change=-100, value_change_lakhs=-100, "
                 "flow_lakhs=-100,price_effect_lakhs=0,action='exited'")
    conn.commit()
    results = generate_batch(conn, MONTH)
    assert len(results) == 1 and results[0]['status'] == 'generated'
    assert 'Fully exited' in results[0]['text']


def test_unknown_scheme_rejected_before_any_cache_write(seeded, capsys):
    conn, path = seeded
    with pytest.raises(SystemExit):
        main(['--db', str(path), '--month', MONTH, '--schemes', '1', '999'])
    assert conn.execute('SELECT COUNT(*) FROM fund_summaries').fetchone()[0] == 0
    assert 'No scheme' in capsys.readouterr().err


def test_cli_missing_credentials_falls_back_without_network(seeded, monkeypatch, capsys):
    _, path = seeded
    monkeypatch.delenv('ZAI_API_KEY', raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail('Missing credentials must not send a network request')

    monkeypatch.setattr('requests.sessions.Session.post', forbidden)
    assert main(['--db', str(path), '--month', MONTH, '--provider', 'zai']) == 0
    report = json.loads(capsys.readouterr().out)[0]
    assert report['status'] == 'fallback'
    assert report['generated_by'] == 'rules'
