"""Safe pinned v1 arithmetic stays reproducible; unsafe consumed inputs fail."""
import sqlite3

from fastapi.testclient import TestClient

from findit.core.rules import LEGACY_RULE_VERSION, RULE_VERSION
from findit.core.watchlist import monthly_report, render_report
from findit.store.releases import snapshot
from findit.web.app import create_app
from tests.test_web import _copy_db


def test_safe_legacy_report_and_history_reopen_on_new_release(tmp_path):
    source = _copy_db(tmp_path)
    folder = tmp_path / 'releases'
    old = snapshot(source, folder, 'Old rules', rules_version=LEGACY_RULE_VERSION)
    old_client = TestClient(create_app(folder / f"{old['release_id']}.db"))
    query = {'month': '2026-08', 'stocks': 'INE002A01018', 'release': old['release_id'], 'rules': LEGACY_RULE_VERSION}
    download = old_client.get('/watchlist/report', params=query)
    history = old_client.get('/api/history/INE002A01018?start=2026-07&end=2026-08').json()
    new = snapshot(source, folder, 'New rules', parent=old['release_id'], notes='Correct numeric availability')
    assert old['release_id'] != new['release_id']
    client = TestClient(create_app(folder / f"{new['release_id']}.db"))
    assert client.get('/watchlist/report', params=query).content == download.content
    for point in history['points']:
        evidence = client.get(point['evidence_url'].replace('/evidence/', '/api/evidence/'))
        assert evidence.status_code == 200, evidence.text
        assert evidence.json()['rule_version'] == LEGACY_RULE_VERSION
        assert evidence.json()['current_shares'] == point['shares']
    assert client.get('/api/watchlist', params=query).json()['rule_version'] == LEGACY_RULE_VERSION
    query['rules'] = RULE_VERSION
    assert client.get('/api/watchlist', params=query).status_code == 409


def test_legacy_unknown_nav_rejects_evidence_only(tmp_path):
    source = _copy_db(tmp_path)
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE mf_holdings_monthly SET pct_nav=NULL WHERE isin='INE002A01018' AND report_month='2026-08'")
    folder = tmp_path / 'releases'
    old = snapshot(source, folder, 'Old rules', rules_version=LEGACY_RULE_VERSION)
    new = snapshot(source, folder, 'Corrected', parent=old['release_id'], notes='Expose missing NAV')
    client = TestClient(create_app(folder / f"{new['release_id']}.db"))
    params = {'month': '2026-08', 'release': old['release_id'], 'rules': LEGACY_RULE_VERSION}
    report = client.get('/api/watchlist', params={**params, 'stocks': 'INE002A01018'})
    assert report.status_code == 200
    history = client.get('/api/history/INE002A01018', params={**params, 'start': '2026-07', 'end': '2026-08'})
    assert history.status_code == 200
    rejected = client.get('/api/evidence/INE002A01018', params=params)
    assert rejected.status_code == 409
    successor = rejected.json()['detail']['successor_url']
    assert new['release_id'] in successor and RULE_VERSION in successor
    reopened = client.get(successor.replace('/evidence/', '/api/evidence/'))
    assert reopened.status_code == 200
    assert all(f['pct_nav_curr'] is None for f in reopened.json()['funds'])


def test_legacy_null_quantity_or_value_never_becomes_zero(tmp_path):
    source = _copy_db(tmp_path)
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE mf_holdings_monthly SET quantity=NULL WHERE scheme_id=1 AND isin='INE002A01018' AND report_month='2026-08'")
    old = snapshot(source, tmp_path / 'releases', 'Unsafe old rules', rules_version=LEGACY_RULE_VERSION)
    client = TestClient(create_app(tmp_path / 'releases' / f"{old['release_id']}.db"))
    assert client.get('/api/watchlist?month=2026-08&stocks=INE002A01018').status_code == 409
    assert client.get('/api/history/INE002A01018?start=2026-07&end=2026-08').status_code == 409


def test_legacy_render_keeps_original_local_instructions(tmp_path):
    with sqlite3.connect(_copy_db(tmp_path)) as conn:
        report = monthly_report(conn, '2026-08', ['INE002A01018'], rules_version=LEGACY_RULE_VERSION)
    assert 'Start your local FindIt server' in render_report(report)
    assert report['rule_version'] == LEGACY_RULE_VERSION


def test_legacy_nonfinite_stored_flow_returns_conflict(tmp_path):
    source = _copy_db(tmp_path)
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE mf_holding_deltas SET flow_lakhs=? WHERE scheme_id=1 AND isin='INE002A01018'", (float('inf'),))
    # SQLite's inf cannot be given a content identity. A controlled ValueError
    # rejects that archive, rather than producing a replayable unsafe release.
    import pytest
    with pytest.raises(ValueError):
        snapshot(source, tmp_path / 'releases', 'Unsafe nonfinite archive', rules_version=LEGACY_RULE_VERSION)
    # NULL is representable in old snapshots but cannot reproduce a finite flow.
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE mf_holding_deltas SET flow_lakhs=NULL WHERE scheme_id=1 AND isin='INE002A01018'")
    old = snapshot(source, tmp_path / 'releases', 'Unsafe unpriced old rules', rules_version=LEGACY_RULE_VERSION)
    client = TestClient(create_app(tmp_path / 'releases' / f"{old['release_id']}.db"))
    assert client.get('/api/watchlist?month=2026-08&stocks=INE002A01018').status_code == 409
    assert client.get('/api/history/INE002A01018?start=2026-07&end=2026-08').status_code == 409
