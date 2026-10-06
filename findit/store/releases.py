"""Content identities and immutable local releases of a verified database."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

from findit.core.rules import (RULE_VERSION, LEGACY_RULE_VERSION as LEGACY_RULE_VERSION,
                               SUPPORTED_RULE_VERSIONS as SUPPORTED_RULE_VERSIONS,
                               require_supported_rules)
from findit.store import queries

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
    try:
        stored = json.loads(manifest.read_text())
    except (ValueError, OSError) as exc:
        raise ValueError("retained release manifest could not be read") from exc
    if not isinstance(stored, dict) or stored.get("release_id") != release_id or hashlib.sha256(target.read_bytes()).hexdigest() != stored.get("database_sha256"):
        raise ValueError("retained release checksum does not match")
    require_supported_rules(stored.get("rule_version"))
    return stored


def content_id(conn: sqlite3.Connection, rules_version: str = RULE_VERSION) -> str:
    digest = hashlib.sha256(require_supported_rules(rules_version).encode())
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



def activation_guard(conn: sqlite3.Connection, report_month: str | None,
                     review_notes: str, rules_version: str = RULE_VERSION) -> dict:
    """Require a reviewed, source-backed monthly comparison before activation."""
    import re
    require_supported_rules(rules_version)
    if not report_month or not re.fullmatch(r"\d{4}-\d{2}", report_month):
        raise ValueError("activation requires an explicit report month (YYYY-MM)")
    try:
        month_start = date.fromisoformat(report_month + "-01")
        previous = (month_start - timedelta(days=1)).strftime("%Y-%m")
    except (ValueError, OverflowError) as exc:
        raise ValueError("activation requires a valid report month") from exc
    if not review_notes.strip():
        raise ValueError("activation requires recorded source/anomaly review notes")
    if not queries.has_table(conn, "ingest_runs"):
        raise ValueError("activation requires a completed monthly pipeline run")
    latest = conn.execute(
        "SELECT run_id,status,validation_report_json FROM ingest_runs "
        "ORDER BY started_at DESC,run_id DESC LIMIT 1").fetchone()
    if not latest:
        raise ValueError("activation requires a completed monthly pipeline run")
    if latest[1] != "completed":
        raise ValueError("latest refresh did not complete; last good release retained")
    try:
        run_report = json.loads(latest[2] or "{}")
    except ValueError as exc:
        raise ValueError("latest monthly run report is invalid") from exc
    if not isinstance(run_report, dict) or run_report.get("curr") != report_month or run_report.get("prev") != previous:
        raise ValueError("latest completed run does not match the requested adjacent report months")
    required = ("snapshot_sources", "disclosure_sources", "holding_evidence", "scheme_month_status")
    if not all(queries.has_table(conn, table) for table in required):
        raise ValueError("activation requires actual-month original-source provenance")
    # A comparison is source-backed only when both validated snapshots have
    # recorded workbook metadata and every held domestic equity has a source row.
    source_backed = {}
    for month in (previous, report_month):
        # Enumerate one candidate per scheme-month. Starting from holdings
        # repeats the portfolio-wide provenance check for every security.
        ids = {int(row[0]) for row in conn.execute(
            "SELECT checked.scheme_id FROM scheme_month_status checked "
            "WHERE checked.report_month=? AND checked.status='ok' "
            "AND EXISTS (SELECT 1 FROM mf_holdings_monthly h JOIN stocks st "
            "ON st.isin=h.isin WHERE h.scheme_id=checked.scheme_id "
            "AND h.report_month=checked.report_month AND st.instrument_type='equity') "
            "AND EXISTS (SELECT 1 FROM snapshot_sources ss JOIN disclosure_sources ds "
            "ON ds.sha256=ss.source_sha256 WHERE ss.scheme_id=checked.scheme_id "
            "AND ss.report_month=checked.report_month) "
            "AND NOT EXISTS (SELECT 1 FROM mf_holdings_monthly held JOIN stocks security "
            "ON security.isin=held.isin WHERE held.scheme_id=checked.scheme_id "
            "AND held.report_month=checked.report_month AND security.instrument_type='equity' "
            "AND NOT EXISTS (SELECT 1 FROM holding_evidence e JOIN snapshot_sources ss "
            "ON ss.scheme_id=e.scheme_id AND ss.report_month=e.report_month "
            "AND ss.source_sha256=e.source_sha256 AND ss.parser_version=e.parser_version "
            "JOIN disclosure_sources ds ON ds.sha256=ss.source_sha256 "
            "WHERE e.scheme_id=held.scheme_id AND e.report_month=held.report_month "
            "AND e.isin=held.isin))", (month,))}
        source_backed[month] = ids
    if not source_backed[report_month]:
        raise ValueError("activation requires actual-month original-source provenance")
    from findit.core.consensus_signals import comparison_rows
    from findit.core.coverage import audit
    rows = comparison_rows(conn, report_month, True)
    ids = set(int(v) for v in rows["scheme_id"]) if not rows.empty else set()
    usable = ids & source_backed[previous] & source_backed[report_month]
    if not usable:
        raise ValueError("activation requires a validated source-backed adjacent domestic-equity comparison")
    return {"report_month": report_month, "previous_month": previous,
            "rule_version": rules_version, "review_notes": review_notes.strip(),
            "run_id": latest[0], "source_backed_compared_funds": sorted(usable),
            "coverage": audit(conn, [report_month]),
            "checked_at": datetime.now(timezone.utc).isoformat()}


def activate_release(output: Path, release_id: str, report_month: str | None = None,
                     notes: str = "") -> dict:
    """Atomically promote or roll back only after the shared monthly guard."""
    import tempfile
    import uuid
    output = output.resolve()
    path, stored = ReleaseStore(output / f"{release_id}.db", output).retained(release_id)
    recorded_month = stored.get("report_month")
    if recorded_month and report_month and recorded_month != report_month:
        raise ValueError("requested report month does not match the retained release")
    report_month = report_month or recorded_month
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        receipt = activation_guard(conn, report_month, notes, stored["rule_version"])
    receipt.update(release_id=release_id, database_sha256=stored["database_sha256"])
    # Write the receipt first; a failure leaves the previous pointer intact.
    receipt_name = f"{release_id}.activation-{uuid.uuid4().hex}.json"
    with tempfile.TemporaryDirectory(prefix=".activation-", dir=output) as folder:
        staged_receipt = Path(folder) / "receipt.json"
        staged_receipt.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n")
        staged_receipt.replace(output / receipt_name)
        pointer = Path(folder) / "current.json"
        pointer.write_text(json.dumps({"release_id": release_id, "report_month": report_month,
                                       "activation_receipt": receipt_name}) + "\n")
        pointer.replace(output / "current.json")
    return receipt


def snapshot(source: Path, output: Path, label: str, parent: str | None = None,
             notes: str = "", *, activate: bool = False, report_month: str | None = None,
             rules_version: str = RULE_VERSION) -> dict:
    """Archive an immutable candidate; monthly activation is an explicit action."""
    import tempfile
    require_supported_rules(rules_version)
    output = output.resolve()
    if parent:
        verify_release(output, parent)
        if not notes.strip():
            raise ValueError("a revision needs an explanation")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".release-", dir=output) as folder:
        staged = Path(folder) / "snapshot.db"
        src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
        dst = sqlite3.connect(staged)
        try:
            src.backup(dst)
        finally:
            src.close()
        try:
            dst.execute("PRAGMA journal_mode=DELETE")
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("database integrity check failed")
            if queries.has_table(dst, "ingest_runs"):
                latest = dst.execute("SELECT status FROM ingest_runs ORDER BY started_at DESC,run_id DESC LIMIT 1").fetchone()
                if latest and latest[0] != "completed":
                    raise ValueError("latest refresh did not complete; last good release retained")
            if activate:
                activation_guard(dst, report_month, notes, rules_version)
            release_id = content_id(dst, rules_version)
            counts = {table: dst.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                      for table in CONTENT_TABLES if queries.has_table(dst, table)}
        finally:
            dst.close()
        target = output / f"{release_id}.db"
        manifest_path = output / f"{release_id}.json"
        if target.exists():
            stored = verify_release(output, release_id)
            if activate:
                activate_release(output, release_id, report_month, notes)
            return stored
        manifest = {"release_id": release_id, "rule_version": rules_version, "label": label,
                    "parent_release": parent, "revision_notes": notes,
                    "report_month": report_month,
                    "created_at": datetime.now(timezone.utc).isoformat(), "counts": counts,
                    "database_sha256": hashlib.sha256(staged.read_bytes()).hexdigest()}
        staged.replace(target)
        temporary_manifest = Path(folder) / "manifest.json"
        temporary_manifest.write_text(json.dumps(manifest, indent=2) + "\n")
        temporary_manifest.replace(manifest_path)
        if activate:
            activate_release(output, release_id, report_month, notes)
        return manifest


@dataclass(frozen=True)
class ReleaseContext:
    connection: sqlite3.Connection
    release_id: str
    rules_version: str

    def __iter__(self):
        # Existing two-value callers can migrate without changing identity.
        yield self.connection
        yield self.release_id


class ReleaseStore:
    """Resolve only local retained releases; cache verification until files change.

    Mutable databases are deliberately never cached. A read transaction ties their
    identity to the exact rows used by the caller, including WAL-backed updates.
    """

    def __init__(self, current: Path, directory: Path | None = None):
        self.current = current.resolve()
        self.directory = Path(directory or (self.current.parent if len(self.current.stem) == 64
                                           else self.current.parent / "releases")).resolve()
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
        # Byte-verified, journal-free snapshots are immutable even when older
        # releases retain a WAL header. Reading must not create new sidecars.
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
            if content_id(conn, stored["rule_version"]) != release_id:
                raise ValueError("retained release content identity does not match")
        self._verified[release_id] = (signatures, stored)
        return path, stored

    def connect(self, release_id: str | None = None, rules: str | None = None) -> ReleaseContext:
        if rules:
            require_supported_rules(rules)
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
        rules_version = metadata["rule_version"] if metadata else (rules or RULE_VERSION)
        if rules and rules_version != rules:
            raise ValueError("requested calculation rules do not match the retained release")
        uri = path.as_uri() + "?mode=ro" + ("&immutable=1" if metadata else "")
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        try:
            identity = metadata["release_id"] if metadata else content_id(conn, rules_version)
            if release_id and identity != release_id:
                raise ValueError("requested release is unavailable or mismatched; current data was not substituted")
            return ReleaseContext(conn, identity, rules_version)
        except Exception:
            conn.close()
            raise
