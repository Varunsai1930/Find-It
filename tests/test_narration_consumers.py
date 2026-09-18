"""Cached narration reaches both consumers without network or database writes."""
import hashlib
import sqlite3

from fastapi.testclient import TestClient

import db
import fallback_summary
from findit.cli.digest import build_digest
from findit.narrate.service import generate_batch
from findit.web.app import create_app


class Editor:
    model_version = 'zai:glm-5.3:editorial-v1'

    def __init__(self):
        self.calls = 0

    def generate(self, prompt):
        self.calls += 1
        return {'sentences': [
            {'fact_id': fact['id'], 'variant': len(fact['variants']) - 1}
            for fact in reversed(prompt['facts'])
        ]}


def seed(path):
    conn = db.get_connection(str(path))
    conn.execute("INSERT INTO schemes (scheme_id,amc_name,scheme_name) VALUES (1,'Test AMC','Test Fund')")
    conn.execute("INSERT INTO stocks (isin,name,instrument_type) VALUES ('INE002A01018','Example Equity','equity')")
    for month, qty, value in [('2026-07', 100, 100), ('2026-08', 120, 180)]:
        conn.execute(
            'INSERT INTO mf_holdings_monthly (scheme_id,isin,report_month,quantity,market_value_lakhs,pct_nav) VALUES (1,?,?,?,?,100)',
            ('INE002A01018',month,qty,value),
        )
        conn.execute(
            "INSERT INTO scheme_month_status (scheme_id,report_month,status,validation_report_json) VALUES (1,?,'ok','{}')",
            (month,),
        )
    conn.execute(
        '''INSERT INTO mf_holding_deltas
        (scheme_id,isin,report_month,prev_month,qty_change,value_change_lakhs,
         flow_lakhs,price_effect_lakhs,pct_nav_change,action)
        VALUES (1,'INE002A01018','2026-08','2026-07',20,80,30,50,0,'added')'''
    )
    conn.commit()
    return conn


def digest_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_api_and_digest_use_fresh_cache_without_network(tmp_path, monkeypatch):
    path = tmp_path / 'consumer.db'
    conn = seed(path)
    editor = Editor()
    generate_batch(conn, '2026-08', [1], provider=editor)
    assert editor.calls == 1
    expected = conn.execute('SELECT summary_text FROM fund_summaries').fetchone()[0]
    conn.close()
    before = digest_hash(path)

    def no_network(*args, **kwargs):
        raise AssertionError('Consumers must not call a provider')

    monkeypatch.setattr('requests.sessions.Session.request', no_network)
    response = TestClient(create_app(str(path))).get('/api/summary/1/2026-08')
    assert response.status_code == 200
    body = response.json()
    assert body['summary'] == expected
    assert body['generated_by'] == 'zai'
    assert body['cached'] is True
    readonly = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    assert expected in build_digest(readonly, [1], '2026-08')
    readonly.close()
    assert digest_hash(path) == before


def test_data_changes_invalidate_both_consumers_and_digest_memory(tmp_path):
    path = tmp_path / 'stale.db'
    conn = seed(path)
    generate_batch(conn, '2026-08', [1], provider=Editor())
    original = build_digest(conn, [1], '2026-08')
    conn.execute('UPDATE mf_holding_deltas SET flow_lakhs=50,price_effect_lakhs=30')
    conn.commit()
    expected = fallback_summary.build_summary(conn, 1, '2026-08')
    updated = build_digest(conn, [1], '2026-08')
    assert expected in updated
    assert updated != original
    conn.close()
    body = TestClient(create_app(str(path))).get('/api/summary/1/2026-08').json()
    assert body['cached'] is False
    assert body['summary'] == expected


def test_quarantine_overrides_existing_ai_cache(tmp_path):
    path = tmp_path / 'quarantine.db'
    conn = seed(path)
    generate_batch(conn, '2026-08', [1], provider=Editor())
    conn.execute("UPDATE scheme_month_status SET status='quarantined' WHERE report_month='2026-08'")
    conn.commit()
    assert 'withheld' in build_digest(conn, [1], '2026-08')
    conn.close()
    body = TestClient(create_app(str(path))).get('/api/summary/1/2026-08').json()
    assert body['quarantined'] is True
    assert body['has_data'] is False
    assert 'withheld' in body['summary']


def test_previous_month_quarantine_withholds_cached_comparison(tmp_path):
    path = tmp_path / 'previous-quarantine.db'
    conn = seed(path)
    generate_batch(conn, '2026-08', [1], provider=Editor())
    conn.execute("UPDATE scheme_month_status SET status='quarantined' WHERE report_month='2026-07'")
    conn.commit()
    assert 'withheld' in build_digest(conn, [1], '2026-08')
    conn.close()
    body = TestClient(create_app(str(path))).get('/api/summary/1/2026-08').json()
    assert body['has_data'] is False
    assert body['cached'] is False
    assert body['summary_status'] == 'quarantined'
