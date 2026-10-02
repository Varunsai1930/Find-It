import sqlite3

from findit.core.history import stock_history
from tests.test_web import _copy_db


def test_history_uses_one_cohort_and_never_fills_missing_month_with_zero(tmp_path):
    c = sqlite3.connect(_copy_db(tmp_path))
    h = stock_history(c, "INE002A01018", "2026-07", "2026-08")
    assert h["cohort"] == [1, 2, 3]
    assert [p["shares"] for p in h["points"]] == [210, 300]
    assert h["points"][1]["net_share_change"] == 90
    # September has no sources/comparison. It cannot manufacture exits.
    gap = stock_history(c, "INE002A01018", "2026-07", "2026-09")
    assert gap["cohort"] == [] and all(p["shares"] is None for p in gap["points"])
    c.execute("DELETE FROM scheme_month_status WHERE scheme_id=3 AND report_month='2026-07'")
    changed = stock_history(c, "INE002A01018", "2026-07", "2026-08")
    assert changed["cohort"] == [1, 2]
    assert [p["shares"] for p in changed["points"]] == [140, 210]
    unknown = stock_history(c, "INE123A01012", "2026-07", "2026-08")
    assert all(p["shares"] is None for p in unknown["points"])


def test_history_links_reproduce_cohort_and_baseline(tmp_path):
    from fastapi.testclient import TestClient
    from findit.web.app import create_app
    path = _copy_db(tmp_path)
    # Exclude a fund from the first comparison only; it remains in August's
    # broader comparison. Three snapshots exercise true range-wide scope.
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO mf_holdings_monthly(scheme_id,report_month,isin,quantity,market_value_lakhs,pct_nav) SELECT scheme_id, '2026-06', isin, quantity, market_value_lakhs, pct_nav FROM mf_holdings_monthly WHERE report_month='2026-07'")
        c.execute("INSERT INTO scheme_month_status(scheme_id,report_month,status) SELECT scheme_id,'2026-06','ok' FROM scheme_month_status WHERE report_month='2026-07' AND scheme_id<>3")
        from findit.core.delta_calculator import compute_deltas
        compute_deltas(c, '2026-06', '2026-07').to_sql('mf_holding_deltas', c, if_exists='append', index=False)
        c.commit()
    client = TestClient(create_app(path))
    h = client.get('/api/history/INE002A01018', params={'start':'2026-06','end':'2026-08'}).json()
    assert h['cohort'] == [1, 2]
    for point in h['points']:
        url = point['evidence_url'].replace('/evidence/', '/api/evidence/')
        response = client.get(url)
        assert response.status_code == 200, response.text
        e = response.json()
        assert e['current_shares'] == point['shares']
        assert e['net_share_change'] == point['net_share_change']
        assert {f['scheme_id'] for f in e['funds']} <= set(h['cohort'])
        assert client.get(point['evidence_url']).status_code == 200
    assert client.get(url.replace('start=2026-06','start=2026-07')).status_code == 400
    assert client.get(url.replace('active_only=1','active_only=0')).status_code == 400
    assert client.get(url.replace('month=2026-08','month=2026-09')).status_code == 400
    broad = client.get('/api/evidence/INE002A01018?month=2026-08').json()
    assert broad['net_share_change'] != e['net_share_change']
    gap = client.get('/api/history/INE002A01018?start=2026-06&end=2026-09').json()
    assert all(p['evidence_url'] is None for p in gap['points'])


def test_history_house_form_keeps_the_retained_release_and_range(tmp_path):
    from bs4 import BeautifulSoup
    from fastapi.testclient import TestClient
    from findit.store.releases import snapshot
    from findit.web.app import create_app
    path = _copy_db(tmp_path)
    folder = tmp_path / "releases"
    retained = snapshot(path, folder, "A")
    with sqlite3.connect(path) as c:
        c.execute("UPDATE mf_holdings_monthly SET quantity=quantity+17 "
                  "WHERE report_month='2026-08'")
    current = snapshot(path, folder, "B")
    client = TestClient(create_app(folder / f"{current['release_id']}.db"))
    params = {"start": "2026-07", "end": "2026-08", "active_only": 1,
              "release": retained["release_id"], "rules": retained["rule_version"]}
    page = client.get("/history/INE002A01018", params=params)
    form = BeautifulSoup(page.text, "html.parser").find("form", attrs={"aria-label": "History fund-house scope"})
    assert form is not None and form["method"] == "get"
    assert {o["value"] for o in form.select("select[name=amc] option")} == {"", "A AMC", "B AMC"}
    submitted = {i["name"]: i["value"] for i in form.select("input[name]")}
    assert submitted == {k: str(v) for k, v in params.items()}
    submitted["amc"] = "A AMC"
    filtered = client.get("/api/history/INE002A01018", params=submitted).json()
    assert filtered["release_id"] == retained["release_id"]
    assert filtered["cohort"] == [1, 2]
    assert filtered["points"][-1]["net_share_change"] == 70
    selected = client.get("/history/INE002A01018", params=submitted)
    assert 'value="A AMC" selected' in selected.text
    evidence = client.get(filtered["points"][-1]["evidence_url"].replace("/evidence/", "/api/evidence/")).json()
    assert evidence["net_share_change"] == 70
    assert {f["amc_name"] for f in evidence["funds"]} == {"A AMC"}
    # A house selection cannot turn a missing month into a zero comparison.
    submitted["end"] = "2026-09"
    gap = client.get("/api/history/INE002A01018", params=submitted).json()
    assert gap["cohort"] == [] and all(p["shares"] is None for p in gap["points"])
