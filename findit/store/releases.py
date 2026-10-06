"""Content identities and immutable local releases of a verified database."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
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
             notes: str = "", *, activate: bool = True) -> dict:
    """Freeze verified files; activate=False leaves the current pointer untouched."""
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
            # backup() preserves WAL mode. Freeze a self-contained database so
            # read-only clients cannot create journals beside the retained file.
            dst.execute("PRAGMA journal_mode=DELETE")
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
            if activate:
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
        if activate:
            pointer = Path(folder) / "current.json"
            pointer.write_text(json.dumps({"release_id": release_id}) + "\n")
            pointer.replace(output / "current.json")
        return manifest


class ReleaseStore:
    """Resolve only local retained releases; cache verification until files change.

    Mutable databases are deliberately never cached. A read transaction ties their
    identity to the exact rows used by the caller, including WAL-backed updates.
    """

    def __init__(self, current: Path, directory: Path | None = None):
        self.current = current.resolve()
        self.directory = directory or (self.current.parent if len(self.current.stem) == 64
                                       else self.current.parent / "releases")
        self._verified: dict[str, tuple] = {}

    def retained(self, release_id: str) -> tuple[Path, dict]:
        import re
        if not re.fullmatch(r"[a-f0-9]{64}", release_id):
            raise ValueError("invalid release ID")
        path = self.directory / f"{release_id}.db"
        manifest = path.with_suffix(".json")
        try:
            signatures = tuple((p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns,
                                p.stat().st_ctime_ns) for p in (path, manifest))
        except OSError as exc:
            raise ValueError("retained release is unavailable; reopen with its retained database and manifest") from exc
        if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal")):
            raise ValueError("retained release has a mutable journal")
        cached = self._verified.get(release_id)
        if cached and cached[0] == signatures:
            return path, cached[1]
        stored = verify_release(self.directory, release_id)
        if stored.get("rule_version") != RULE_VERSION:
            raise ValueError("release calculation rules are unsupported by this application version")
        # Byte-verified, journal-free snapshots are immutable even when older
        # releases retain a WAL header. Reading must not create new sidecars.
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
            if content_id(conn) != release_id:
                raise ValueError("retained release content identity does not match")
        self._verified[release_id] = (signatures, stored)
        return path, stored

    def connect(self, release_id: str | None = None, rules: str | None = None):
        if rules and rules != RULE_VERSION:
            raise ValueError("requested calculation rules do not match this application version")
        import re
        retained_id = release_id or (self.current.stem if re.fullmatch(r"[a-f0-9]{64}", self.current.stem) else None)
        if retained_id:
            try:
                path, metadata = self.retained(retained_id)
            except ValueError:
                # A mutable report may still be reproduced while the current DB
                # has exactly that content. Never substitute different content.
                if (not release_id or self.current.parent == self.directory or
                        (self.directory / f"{retained_id}.db").exists() or
                        (self.directory / f"{retained_id}.json").exists()):
                    raise
                path, metadata = self.current, None
        else:
            path, metadata = self.current, None
        uri = path.as_uri() + "?mode=ro" + ("&immutable=1" if metadata else "")
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        try:
            identity = metadata["release_id"] if metadata else content_id(conn)
            if release_id and identity != release_id:
                raise ValueError("requested release is unavailable or mismatched; current data was not substituted")
            return conn, identity
        except Exception:
            conn.close()
            raise
