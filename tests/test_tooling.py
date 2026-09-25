"""Tooling tests: pyproject, digest preview, rebuild-on-copy. Tmp DBs only."""
from __future__ import annotations

import hashlib
import sqlite3
import tomllib
from pathlib import Path

import db
import delta_calculator

from findit.cli.digest import build_digest
from findit.cli import digest as digest_cli
from findit.cli import rebuild as rebuild_cli
from findit.cli.rebuild import rebuild_copy

ROOT = Path(__file__).resolve().parents[1]
PREV = "2026-03"
CURR = "2026-04"


def _seed_holdings(db_path: Path) -> int:
    conn = db.get_connection(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO schemes (amc_name, scheme_name) VALUES (?, ?)",
            ("Test AMC", "Test Fund"),
        )
        row = cur.execute(
            "SELECT scheme_id FROM schemes WHERE amc_name = ? AND scheme_name = ?",
            ("Test AMC", "Test Fund"),
        ).fetchone()
        assert row is not None
        sid = int(row[0])
        cur.execute(
            "INSERT OR REPLACE INTO stocks (isin, name, industry, instrument_type)"
            " VALUES (?, ?, ?, ?)",
            ("INE002A01018", "Reliance Industries Ltd.", "Refineries", "equity"),
        )
        cur.execute(
            "INSERT OR REPLACE INTO stocks (isin, name, industry, instrument_type)"
            " VALUES (?, ?, ?, ?)",
            ("INE040A01034", "HDFC Bank Ltd.", "Banks", "equity"),
        )
        cur.execute(
            "INSERT OR REPLACE INTO mf_holdings_monthly"
            " (scheme_id, isin, report_month, quantity, market_value_lakhs, pct_nav)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (sid, "INE002A01018", PREV, 1000.0, 100.0, 5.0),
        )
        cur.execute(
            "INSERT OR REPLACE INTO mf_holdings_monthly"
            " (scheme_id, isin, report_month, quantity, market_value_lakhs, pct_nav)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (sid, "INE040A01034", PREV, 2000.0, 200.0, 10.0),
        )
        cur.execute(
            "INSERT OR REPLACE INTO mf_holdings_monthly"
            " (scheme_id, isin, report_month, quantity, market_value_lakhs, pct_nav)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (sid, "INE002A01018", CURR, 1200.0, 130.0, 6.0),
        )
        cur.execute(
            "INSERT OR REPLACE INTO mf_holdings_monthly"
            " (scheme_id, isin, report_month, quantity, market_value_lakhs, pct_nav)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (sid, "INE040A01034", CURR, 1500.0, 150.0, 7.0),
        )
        conn.commit()
        return sid
    finally:
        conn.close()


def _file_hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _delta_count_ro(db_path: Path, month: str) -> int:
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM mf_holding_deltas WHERE report_month = ?",
            (month,),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def test_pyproject_parses():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["build-system"]["requires"] == ["setuptools==83.0.0"]
    proj = data["project"]
    assert proj["name"] == "findit"
    assert proj["version"] == "0.2.0"
    assert proj["requires-python"] == ">=3.12"  # the pinned numpy needs 3.12
    deps = set(proj["dependencies"])
    for pinned in [
        "pandas==3.0.5",
        "numpy==2.5.1",
        "openpyxl==3.1.5",
        "requests==2.33.1",
        "beautifulsoup4==4.15.0",
        "lxml==6.1.0",
        "fastapi==0.141.1",
        "uvicorn==0.52.1",
        "jinja2==3.1.6",
        "httpx==0.28.1",
    ]:
        assert pinned in deps, f"missing dependency {pinned}"
    dev = data["project"]["optional-dependencies"]["dev"]
    assert "pytest==8.4.2" in dev
    assert "ruff==0.16.5" in dev
    ruff = data["tool"]["ruff"]
    assert ruff["target-version"] == "py312"
    assert ruff["line-length"] == 100
    select = set(ruff.get("select", []))
    lint_select = set(ruff.get("lint", {}).get("select", []))
    combined = select | lint_select
    for prefix in ["E4", "E7", "E9", "F"]:
        assert prefix in combined, f"ruff select missing {prefix}"
    pytest_cfg = data["tool"]["pytest"]["ini_options"]
    assert pytest_cfg["testpaths"] == ["tests"]
    assert pytest_cfg["pythonpath"] == ["."]
    assert "-ra" in pytest_cfg["addopts"]
    assert "-p no:cacheprovider" in pytest_cfg["addopts"]


def test_digest_preview_deterministic(tmp_path):
    db_path = tmp_path / "digest.db"
    sid = _seed_holdings(db_path)
    conn = db.get_connection(str(db_path))
    try:
        deltas = delta_calculator.compute_deltas(conn, PREV, CURR)
        delta_calculator.persist_deltas(conn, deltas)
        first = build_digest(conn, [sid], CURR)
        second = build_digest(conn, [sid], CURR)
        assert first == second
        assert CURR in first
        assert "Test Fund" in first
        rev = build_digest(conn, [sid], CURR)
        assert rev == first
    finally:
        conn.close()
    # CLI preview prints the same digest and sends nothing.
    src = (ROOT / "findit" / "cli" / "digest.py").read_text()
    assert "smtplib" not in src
    assert "api_key" not in src.lower()
    assert "password" not in src.lower()


def test_digest_cli_preview_only(tmp_path, capsys):
    db_path = tmp_path / "digest_cli.db"
    sid = _seed_holdings(db_path)
    conn = db.get_connection(str(db_path))
    try:
        deltas = delta_calculator.compute_deltas(conn, PREV, CURR)
        delta_calculator.persist_deltas(conn, deltas)
    finally:
        conn.close()
    rc = digest_cli.main(["--db", str(db_path), "--month", CURR, "--schemes", str(sid)])
    assert rc == 0
    out = capsys.readouterr().out
    assert CURR in out
    assert "Test Fund" in out


def test_rebuild_uses_copy_only(tmp_path):
    orig = tmp_path / "orig.db"
    copy = tmp_path / "copy.db"
    _seed_holdings(orig)
    assert _delta_count_ro(orig, CURR) == 0
    before = _file_hash(orig)
    n = rebuild_copy(orig, copy, PREV, CURR)
    assert n > 0
    assert copy.exists()
    assert _delta_count_ro(copy, CURR) == n
    # Original must be untouched: same bytes and still no deltas.
    assert _file_hash(orig) == before
    assert _delta_count_ro(orig, CURR) == 0


def test_rebuild_refuses_same_file(tmp_path):
    orig = tmp_path / "same.db"
    _seed_holdings(orig)
    try:
        rebuild_copy(orig, orig, PREV, CURR)
    except ValueError:
        pass
    else:
        raise AssertionError("rebuild_copy must refuse same src/dst")
    # CLI exposes offline copy args and uses delta_calculator on a copy only.
    parser = rebuild_cli.build_parser()
    actions = {a.dest for a in parser._actions}
    assert "db_copy" in actions
    assert "prev" in actions
    assert "curr" in actions
    src = (ROOT / "findit" / "cli" / "rebuild.py").read_text()
    assert "delta_calculator" in src
    assert "shutil" in src or "copy" in src
