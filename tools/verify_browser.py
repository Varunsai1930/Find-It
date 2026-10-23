"""Exercise the shipped dashboard in Chromium using synthetic, local-only data.

Requires the existing download extra and Playwright's Chromium installation.
Browser requests are fulfilled by TestClient; no listening port or network is used.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://findit.test"


def fixture(root: Path) -> tuple[Path, str]:
    from findit.core.delta_calculator import compute_deltas, persist_deltas
    from findit.store.db import get_connection
    from findit.store.releases import snapshot

    database = root / "synthetic.db"
    conn = get_connection(str(database))
    try:
        for sid in (1, 2):
            conn.execute("INSERT INTO schemes(scheme_id,amc_name,scheme_name,is_active_equity) "
                         "VALUES (?,'Synthetic AMC',?,1)",
                         (sid, "Synthetic Flexi Cap" if sid == 1 else "Synthetic Value"))
            for month in ("2026-07", "2026-08"):
                conn.execute("INSERT INTO scheme_month_status(scheme_id,report_month,status) "
                             "VALUES (?,?,'ok')", (sid, month))
        for number in range(120):
            isin = f"INE{number:03}A01012"
            conn.execute("INSERT INTO stocks(isin,name,instrument_type) VALUES (?,?,'equity')",
                         (isin, f"Synthetic Company {number:03}"))
            for sid in (1, 2):
                for month, quantity in (("2026-07", 100), ("2026-08", 110 + number)):
                    conn.execute("INSERT INTO mf_holdings_monthly "
                                 "(scheme_id,isin,report_month,quantity,market_value_lakhs,pct_nav) "
                                 "VALUES (?,?,?,?,?,0.5)", (sid, isin, month, quantity, quantity / 10))
        persist_deltas(conn, compute_deltas(conn, "2026-07", "2026-08"))
        conn.commit()
    finally:
        conn.close()
    release = snapshot(database, root / "releases", "Synthetic browser regression")
    return root / "releases" / (release["release_id"] + ".db"), release["release_id"]


def verify(root: Path) -> dict:
    from fastapi.testclient import TestClient
    from playwright.sync_api import expect, sync_playwright
    from findit.web.app import create_app

    database, release_id = fixture(root)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    checks = []
    errors = []
    with TestClient(create_app(database, hosted=True, expected_month="2026-08",
                               allowed_hosts=("findit.test",)), base_url=ORIGIN) as client:
        # Compare concurrent scopes with their serial results; release identity
        # and calculation output must stay stable as caches fill and evict.
        paths = ["/api/consensus/2026-08?active_only=1", "/api/consensus/2026-08?active_only=0",
                 "/api/stocks/search?q=Company&month=2026-08",
                 "/api/stock/INE000A01012?month=2026-08", "/api/coverage"]
        serial = [client.get(path).json() for path in paths]
        with ThreadPoolExecutor(max_workers=5) as pool:
            concurrent = list(pool.map(lambda path: client.get(path).json(), paths * 3))
        assert concurrent == serial * 3
        checks.append("concurrent request scopes match serial output")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 900},
                                          reduced_motion="reduce")
            requests = []
            fail_fragment = [False]

            def serve(route):
                request = route.request
                path = request.url.removeprefix(ORIGIN)
                requests.append(path)
                if path.startswith("/fragments/month/") and fail_fragment[0]:
                    fail_fragment[0] = False
                    route.fulfill(status=503, body="Temporary synthetic failure")
                    return
                response = client.request(request.method, path, content=request.post_data_buffer,
                                          headers={"accept-encoding": "identity",
                                                   "content-type": request.headers.get(
                                                       "content-type", "application/json")})
                headers = {key: value for key, value in response.headers.items()
                           if key not in {"content-length", "content-encoding"}}
                route.fulfill(status=response.status_code, headers=headers, body=response.content)

            context.route(ORIGIN + "/**", serve)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.goto(ORIGIN, wait_until="networkidle")
                assert "/api/schemes" not in requests
                page.keyboard.press("Tab")
                expect(page.locator(".skip")).to_be_focused()
                page.keyboard.press("Enter")
                expect(page.locator("#month-view")).to_be_focused()
                checks.append("initial skip link and results focus")

                page.keyboard.press("Control+k")
                expect(page.locator("#stock-search")).to_be_focused()
                page.locator("#stock-search").fill("Company 000")
                expect(page.locator("#stock-options [role=option]").first).to_be_visible()
                page.keyboard.press("ArrowDown")
                assert page.locator("#stock-search").get_attribute("aria-activedescendant")
                page.keyboard.press("Enter")
                expect(page.locator("#stock-box h3")).to_be_focused()
                page.keyboard.press("Escape")
                assert not page.locator("#stock-dialog").evaluate("el => el.open")
                checks.append("keyboard search, selection, detail focus and Escape")

                page.keyboard.press("Control+k")
                page.locator("#stock-search").fill("Flexi")
                page.locator("#stock-options [role=option]").filter(has_text="Synthetic Flexi Cap").click()
                expect(page.locator("#summary-box")).to_contain_text("Flexi Cap")
                assert requests.count("/api/schemes") == 1
                checks.append("fund catalog fetched on demand once and summary loads")

                page.locator('#section-nav a[data-view="activity"]').click()
                page.locator("#limit-select").select_option("0")
                expect(page.locator("#consensus-table tbody tr")).to_have_count(50)
                page.get_by_role("navigation", name="Stock ranking pages").get_by_text("Next", exact=True).click()
                expect(page.locator("#consensus-table tbody tr").first).to_have_attribute("data-rank", "51")
                page.locator("#active-only").uncheck()
                expect(page.locator("#consensus-table tbody tr").first).to_have_attribute("data-rank", "1")
                checks.append("pagination preserves absolute ranks and filters reset page")

                fail_fragment[0] = True
                page.locator("#month-select").select_option("2026-07")
                expect(page.locator("#view-status")).to_contain_text("Previous results remain")
                expect(page.locator("#consensus-table tbody tr")).to_have_count(50)
                page.get_by_role("button", name="Retry", exact=True).click()
                expect(page.locator("#view-status")).to_have_text("Jul 2026 results updated.")
                expect(page.locator("#month-view")).to_contain_text("Jun 2026 portfolios are not loaded")
                page.locator("#month-select").select_option("2026-08")
                expect(page.locator("#view-status")).to_have_text("Aug 2026 results updated.")
                checks.append("failed refresh retains data, retries and announces selected month")

                page.locator("#consensus-table .row-follow").first.click()
                page.locator('#section-nav a[data-view="watchlist"]').click()
                expect(page.locator("#watchlist-report")).to_be_visible()
                with page.expect_download() as downloaded:
                    page.locator("#watchlist-report").click()
                report = Path(downloaded.value.path()).read_text()
                assert release_id in report
                saved = page.evaluate("JSON.parse(localStorage.getItem('findit-watchlist-v1'))")
                assert len(saved["stocks"]) == 1
                page.reload(wait_until="networkidle")
                expect(page.locator("#watchlist-report")).to_be_visible()
                assert page.evaluate("JSON.parse(localStorage.getItem('findit-watchlist-v1'))") == saved
                checks.append("watchlist survives reload and report pins the release")

                minimum_contrast = {}
                for theme in ("light", "dark"):
                    minimum_contrast[theme] = page.evaluate("""theme => {
                      document.documentElement.dataset.theme = theme;
                      const css = getComputedStyle(document.documentElement);
                      const pairs = [['--ink','--bg'],['--ink-2','--surface'],['--ink-3','--surface-2'],
                        ['--ink-3','--accent-soft'],['--pos','--pos-soft'],['--neg','--neg-soft'],
                        ['--warn','--warn-soft'],['--info','--info-soft'],['--accent-ink','--accent']];
                      function light(name) {
                        const hex=css.getPropertyValue(name).trim().replace('#','');
                        const full=hex.length===3 ? hex.split('').map(c=>c+c).join('') : hex;
                        const rgb=[0,2,4].map(i=>parseInt(full.slice(i,i+2),16)/255)
                          .map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
                        return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;
                      }
                      return Math.min(...pairs.map(([a,b])=>
                        (Math.max(light(a),light(b))+.05)/(Math.min(light(a),light(b))+.05)));
                    }""", theme)
                    assert minimum_contrast[theme] >= 4.5, minimum_contrast
                checks.append("text palette contrast at least 4.5:1 in both themes")

                page.goto(ORIGIN, wait_until="networkidle")
                for width in (320, 390, 768, 1280):
                    page.set_viewport_size({"width": width, "height": 844})
                    assert page.evaluate("document.body.scrollWidth <= innerWidth"), width
                    if width <= 640:
                        for selector in ("#search-open", "#theme-toggle", "#consensus-table .row-follow"):
                            rect = page.locator(selector).first.bounding_box()
                            assert rect["width"] >= 44 and rect["height"] >= 44, (width, selector)
                    page.locator("#consensus-table .stock-link").first.click()
                    expect(page.locator("#stock-box h3")).to_be_visible()
                    assert page.locator("#stock-dialog").evaluate("el => el.scrollWidth <= innerWidth")
                    page.keyboard.press("Escape")
                    assert not page.locator("#stock-dialog").evaluate("el => el.open")
                checks.append("responsive page, touch targets and drawers at four widths")
                assert not errors, errors
            except Exception:
                page.screenshot(path=str(root / "browser-failure.png"), full_page=True)
                raise
            finally:
                browser.close()
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    checks.append("retained database bytes unchanged")
    return {"passed": True, "checks": checks, "release_id": release_id,
            "minimum_palette_contrast": minimum_contrast, "javascript_errors": errors,
            "limitations": ["Chromium with synthetic local data; no hosted network measurement.",
                            "Keyboard and accessibility-tree checks do not replace screen-reader testing."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="A new directory for the report and failure screenshot")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)
        result = verify(args.output)
        (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    else:
        with tempfile.TemporaryDirectory(prefix="findit-browser-") as directory:
            result = verify(Path(directory))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
