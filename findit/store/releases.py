"""Content identities and immutable local releases of a verified database."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

from findit.store import queries

RULE_VERSION = "readiness-2026-10-01"
CONTENT_TABLES = ("schemes", "stocks", "mf_holdings_monthly", "mf_holding_deltas",
                  "scheme_month_status", "corporate_actions", "security_prices_monthly",
                  "shareholding_quarterly", "coverage_inventories", "expected_funds",
                  "disclosure_sources", "snapshot_sources", "holding_evidence")


def verify_release(output: Path, release_id: str) -> dict:
    import re
    if not re.fullmatch(r"[a-f0-9]{64}", release_id):
        raise ValueError("invalid release ID")
    target, manifest = output / f"{release_id}.db", output / f"{release_id}.json"
    if not target.is_file() or not manifest.is_file():
        raise ValueError("retained release is unavailable")
    stored = json.loads(manifest.read_text())
    if stored["release_id"] != release_id or hashlib.sha256(target.read_bytes()).hexdigest() != stored["database_sha256"]:
        raise ValueError("retained release checksum does not match")
    return stored


def content_id(conn: sqlite3.Connection) -> str:
    digest = hashlib.sha256(RULE_VERSION.encode())
    for table in CONTENT_TABLES:
        if not queries.has_table(conn, table):
            continue
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        digest.update(json.dumps([table, columns]).encode())
        order = ",".join(f'"{name}"' for name in columns)
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}"):
            digest.update(json.dumps(list(row), separators=(",", ":"), allow_nan=False).encode())
            digest.update(b"\n")
    return digest.hexdigest()


def snapshot(source: Path, output: Path, label: str, parent: str | None = None,
             notes: str = "") -> dict:
    """Copy first, check the copy, then publish immutable files. Original is read-only."""
    import tempfile
    if parent:
        verify_release(output, parent)
        if not notes.strip():
            raise ValueError("a revision needs an explanation")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".release-", dir=output) as folder:
        staged = Path(folder) / "snapshot.db"
        src = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(staged)
        try:
            src.backup(dst)
        finally:
            src.close()
        try:
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("database integrity check failed")
            if queries.has_table(dst, "ingest_runs"):
                latest = dst.execute("SELECT status FROM ingest_runs ORDER BY started_at DESC LIMIT 1").fetchone()
                if latest and latest[0] != "completed":
                    raise ValueError("latest refresh did not complete; last good release retained")
            release_id = content_id(dst)
            counts = {table: dst.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                      for table in CONTENT_TABLES if queries.has_table(dst, table)}
        finally:
            dst.close()
        target = output / f"{release_id}.db"
        manifest_path = output / f"{release_id}.json"
        if target.exists():
            if not manifest_path.exists():
                raise ValueError("existing release is missing its manifest")
            stored = verify_release(output, release_id)
            pointer = Path(folder) / "current.json"
            pointer.write_text(json.dumps({"release_id": release_id}) + "\n")
            pointer.replace(output / "current.json")
            return stored
        manifest = {"release_id": release_id, "rule_version": RULE_VERSION, "label": label,
                    "parent_release": parent, "revision_notes": notes,
                    "created_at": datetime.now(timezone.utc).isoformat(), "counts": counts,
                    "database_sha256": hashlib.sha256(staged.read_bytes()).hexdigest()}
        staged.replace(target)
        temporary_manifest = Path(folder) / "manifest.json"
        temporary_manifest.write_text(json.dumps(manifest, indent=2) + "\n")
        temporary_manifest.replace(manifest_path)
        # An explicit local pointer can be restored to any retained release.
        pointer = Path(folder) / "current.json"
        pointer.write_text(json.dumps({"release_id": release_id}) + "\n")
        pointer.replace(output / "current.json")
        return manifest
