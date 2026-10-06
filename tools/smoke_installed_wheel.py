"""Smoke an installed FindIt wheel from outside the repository, using fake data."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import tempfile

import findit
import httpx

from findit.core.delta_calculator import compute_deltas, persist_deltas
from findit.store.db import get_connection
from findit.store.releases import snapshot
from findit.web.app import create_app


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    args = parser.parse_args()
    package = Path(findit.__file__).resolve()
    if package.is_relative_to(args.checkout.resolve()):
        raise ValueError("Smoke test imported the checkout instead of the installed wheel")
    with tempfile.TemporaryDirectory(prefix="findit-wheel-smoke-") as folder:
        root = Path(folder)
        database = root / "synthetic.db"
        conn = get_connection(str(database))
        conn.execute("INSERT INTO schemes (scheme_id,amc_name,scheme_name,is_active_equity) "
                     "VALUES (1,'Synthetic AMC','Synthetic Flexi Cap',1)")
        conn.execute("INSERT INTO stocks (isin,name,instrument_type) "
                     "VALUES ('INE002A01018','Synthetic Company','equity')")
        for month, quantity in (("2026-07", 100), ("2026-08", 150)):
            conn.execute("INSERT INTO mf_holdings_monthly "
                         "(scheme_id,isin,report_month,quantity,market_value_lakhs,pct_nav) "
                         "VALUES (1,'INE002A01018',?,?,?,10)", (month, quantity, quantity / 10))
            conn.execute("INSERT INTO scheme_month_status (scheme_id,report_month,status) "
                         "VALUES (1,?,'ok')", (month,))
        persist_deltas(conn, compute_deltas(conn, "2026-07", "2026-08"))
        conn.commit()
        conn.close()
        release = snapshot(database, root / "releases", "Installed wheel synthetic fixture")
        frozen = root / "releases" / (release["release_id"] + ".db")
        before = hashlib.sha256(frozen.read_bytes()).hexdigest()
        app = create_app(frozen, hosted=True, expected_month="2026-08",
                         allowed_hosts=("findit-smoke.vercel.app",))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                   base_url="https://findit-smoke.vercel.app")
        urls = ["/health/live", "/health/ready", "/?month=2026-08",
                "/fragments/month/2026-08", "/api/coverage?month=2026-08",
                "/api/stocks/search?q=Synthetic", "/api/stock/INE002A01018?month=2026-08",
                "/evidence/INE002A01018?month=2026-08",
                "/history/INE002A01018?start=2026-07&end=2026-08"]
        for url in urls:
            response = await client.get(url)
            assert response.status_code == 200, (url, response.status_code, response.text[:200])
        dashboard = (await client.get("/?month=2026-08")).text
        assets = set(re.findall(r'(?:href|src)="(/static/[^"?]+)(?:\?[^\"]*)?"', dashboard))
        assert any(path.endswith(".css") for path in assets)
        assert any(path.endswith(".js") for path in assets)
        for asset in assets:
            assert (await client.get(asset)).status_code == 200, asset
        body = {"month": "2026-08", "stocks": ["INE002A01018"], "active_only": True}
        response = await client.post("/api/watchlist", json=body)
        assert response.status_code == 200
        report = response.json()
        body.update(release=report["release_id"], rules=report["rule_version"])
        response = await client.post("/watchlist/report", json=body)
        assert response.status_code == 200
        assert "https://findit-smoke.vercel.app/evidence/" in response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert (await client.get("/health/ready")).json()["release_id"] == release["release_id"]
        assert hashlib.sha256(frozen.read_bytes()).hexdigest() == before
        assert not list((root / "releases").glob("*.db-*"))
        await client.aclose()
        print(json.dumps({"installed_package": str(package), "checked_routes": len(urls) + 2,
                          "checked_assets": len(assets), "unchanged_database": True}))


if __name__ == "__main__":
    asyncio.run(main())
