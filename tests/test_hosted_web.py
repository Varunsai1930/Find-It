"""Hosted boundaries, private reports, and both-snapshot detail validity."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from findit.store.releases import snapshot
from findit.web.app import create_app
from tests.test_web import _copy_db


def test_hosted_reports_use_exact_https_host_and_private_post(tmp_path):
    frozen = snapshot(_copy_db(tmp_path), tmp_path / 'releases', 'Hosted fixture')
    app = create_app(tmp_path / 'releases' / f"{frozen['release_id']}.db",
                     hosted=True, expected_month='2026-08',
                     allowed_hosts=('findit-preview.vercel.app',))
    client = TestClient(app, base_url='https://findit-preview.vercel.app')
    body = {'month': '2026-08', 'stocks': ['INE002A01018'], 'active_only': True}
    result = client.post('/api/watchlist', json=body)
    assert result.status_code == 200
    assert result.headers['cache-control'] == 'private, no-store'
    report = result.json()
    body.update(release=report['release_id'], rules=report['rule_version'])
    download = client.post('/watchlist/report', json=body)
    assert download.status_code == 200
    assert 'https://findit-preview.vercel.app/evidence/' in download.text
    assert 'Sign in if deployment protection requires it' in download.text
    assert 'Start your local' not in download.text
    assert download.headers['cache-control'] == 'private, no-store'
    assert client.post('/watchlist/report', json=body, headers={'host': 'attacker.example'}).status_code == 400
    forwarded = client.post('/watchlist/report', json=body, headers={'x-forwarded-host': 'attacker.example', 'x-forwarded-proto': 'http'})
    assert 'attacker.example' not in forwarded.text
    assert 'https://findit-preview.vercel.app/evidence/' in forwarded.text
    assert client.get('/health/live').json() == {'status': 'alive'}
    assert client.get('/health/ready').json()['release_id'] == frozen['release_id']
    for url in ['/api/watchlist?month=2026-08&stocks=INE002A01018', '/watchlist/report?month=2026-08&stocks=INE002A01018']:
        assert client.get(url).headers['cache-control'] == 'private, no-store'


@pytest.mark.parametrize('scheme_id', [-1, 0, 9223372036854775808, 10**50])
def test_scheme_id_is_bounded_before_database_access(tmp_path, scheme_id):
    client = TestClient(create_app(_copy_db(tmp_path)))
    assert client.get(f'/api/summary/{scheme_id}/2026-08').status_code == 422


def test_previous_quarantine_withholds_action_but_keeps_raw_holding(tmp_path):
    path = _copy_db(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE scheme_month_status SET status='quarantined' WHERE scheme_id=1 AND report_month='2026-07'")
    client = TestClient(create_app(path))
    result = client.get('/api/stock/INE002A01018?month=2026-08').json()
    row = next(h for h in result['holdings'] if h['scheme_id'] == 1)
    assert row['quantity'] == 150 and row['market_value_lakhs'] == 15
    assert row['validation_status'] == 'ok'
    assert row['previous_validation_status'] == 'quarantined'
    assert row['comparison_status'] == 'withheld'
    assert all(row[k] is None for k in ['action', 'qty_change', 'flow_lakhs', 'value_change_lakhs'])
    assert result['withheld_count'] == 1
    html = client.get('/fragments/stock?q=INE002A01018&month=2026-08').text
    assert 'Either snapshot failed validation.' in html
    assert 'raw: added' not in html


def test_readiness_failure_does_not_leak_paths(tmp_path):
    client = TestClient(create_app(tmp_path / 'private-missing.db'))
    response = client.get('/health/ready')
    assert response.status_code == 503 and str(tmp_path) not in response.text
    assert client.get('/health/live').status_code == 200
    with pytest.raises(ValueError, match='exact allowed hosts'):
        create_app(hosted=True)
    with pytest.raises(ValueError, match='exact hostnames'):
        create_app(allowed_hosts=('*.vercel.app',))


def test_post_watchlist_validation(tmp_path):
    client = TestClient(create_app(_copy_db(tmp_path)))
    assert client.post('/api/watchlist', json={'month': '2026-08', 'stocks': ['bad']}).status_code == 422
    assert client.post('/api/watchlist', json={'month': '2026-08', 'stocks': ['INE002A01018'] * 101}).status_code == 422
    assert client.post('/api/watchlist', json={'month': '2026-99', 'stocks': []}).status_code == 422


@pytest.mark.parametrize('change,reason', [
    ("UPDATE mf_holding_deltas SET prev_month='2026-06' WHERE scheme_id=1", 'adjacent'),
    ("DELETE FROM mf_holdings_monthly WHERE scheme_id=1 AND report_month='2026-07'", 'snapshot'),
])
def test_stock_detail_requires_adjacent_actual_previous_snapshot(tmp_path, change, reason):
    path = _copy_db(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO mf_holdings_monthly(scheme_id,isin,report_month,quantity,market_value_lakhs,pct_nav) SELECT scheme_id,isin,'2026-06',quantity,market_value_lakhs,pct_nav FROM mf_holdings_monthly WHERE scheme_id=1 AND report_month='2026-07'")
        conn.execute("INSERT INTO scheme_month_status(scheme_id,report_month,status) VALUES(1,'2026-06','ok')")
        conn.execute(change)
    result = TestClient(create_app(path)).get('/api/stock/INE002A01018?month=2026-08').json()
    row = next(h for h in result['holdings'] if h['scheme_id'] == 1)
    assert row['comparison_status'] == 'unavailable'
    assert reason in row['comparison_reason']
    assert row['quantity'] == 150 and row['action'] is None and row['qty_change'] is None


def test_unsafe_quantity_coverage_is_withheld_and_unknown_value_is_null(tmp_path):
    from findit.core.coverage import month_coverage, house_coverage
    path = _copy_db(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE mf_holdings_monthly SET quantity=NULL WHERE scheme_id=1 AND report_month='2026-07' AND isin='INE002A01018'")
        assert month_coverage(conn, '2026-08')['prev_withheld'] == 1
        assert house_coverage(conn, '2026-08', 'A AMC', {2})['withheld_count'] == 1
        conn.execute("UPDATE mf_holdings_monthly SET market_value_lakhs=NULL WHERE scheme_id=2 AND report_month='2026-08' AND isin='INE002A01018'")
    result = TestClient(create_app(path)).get('/api/stock/INE002A01018?month=2026-08').json()
    row = next(h for h in result['holdings'] if h['scheme_id'] == 2)
    assert row['qty_change'] == 20 and row['flow_lakhs'] is None and row['value_change_lakhs'] is None


def test_verified_overview_cache_isolates_scope_and_mutable_reads(tmp_path, monkeypatch):
    from findit.core import consensus_signals
    original = consensus_signals.fund_house_activity
    calls = []
    def counted(*args, **kwargs):
        calls.append(args[2])
        return original(*args, **kwargs)
    monkeypatch.setattr(consensus_signals, 'fund_house_activity', counted)
    source = _copy_db(tmp_path)
    mutable = TestClient(create_app(source))
    mutable.get('/fragments/month/2026-08')
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE scheme_month_status SET status='quarantined' WHERE scheme_id=3 AND report_month='2026-08'")
    second = mutable.get('/fragments/month/2026-08')
    from tests.test_web import _fact
    assert len(calls) == 2
    assert '<dd class="fact__value">1</dd>' in _fact(second.text, 'Withheld')
    frozen = snapshot(source, tmp_path / 'releases', 'Cache fixture')
    retained = tmp_path / 'releases' / f"{frozen['release_id']}.db"
    client = TestClient(create_app(retained))
    client.get('/fragments/month/2026-08')
    client.get('/fragments/month/2026-08?side=sell&cols=all')
    assert len(calls) == 3
    client.get('/fragments/month/2026-08?active_only=0')
    assert len(calls) == 4 and calls[-1] is False
    retained.write_bytes(b'corrupt')
    assert client.get('/fragments/month/2026-08').status_code == 409
