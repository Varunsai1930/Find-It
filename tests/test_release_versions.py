"""Old retained identities and explicit rules survive a rules upgrade."""
import json
import sqlite3
from pathlib import Path

import pytest

from findit.core.rules import LEGACY_RULE_VERSION, RULE_VERSION
from findit.store.releases import ReleaseStore, content_id, snapshot
from tests.test_web import _copy_db


def test_rule_versions_produce_distinct_logical_ids_and_retained_context(tmp_path):
    source = _copy_db(tmp_path)
    output = tmp_path / "releases"
    with sqlite3.connect(source) as conn:
        assert content_id(conn, LEGACY_RULE_VERSION) != content_id(conn, RULE_VERSION)
        old_id = content_id(conn, LEGACY_RULE_VERSION)
    old = snapshot(source, output, "Old", rules_version=LEGACY_RULE_VERSION)
    assert old["release_id"] == old_id
    current = snapshot(source, output, "New")
    store = ReleaseStore(output / f"{current['release_id']}.db")
    context = store.connect(old_id, LEGACY_RULE_VERSION)
    try:
        assert context.rules_version == LEGACY_RULE_VERSION
        assert context.release_id == old_id
        conn, identity = context
        assert conn is context.connection and identity == old_id
    finally:
        context.connection.close()
    with pytest.raises(ValueError, match="do not match"):
        store.connect(old_id, RULE_VERSION)
    with pytest.raises(ValueError, match="unsupported"):
        store.connect(old_id, "arbitrary-rules")


def test_manifest_rules_determine_logical_verification(tmp_path):
    source = _copy_db(tmp_path)
    output = tmp_path / "releases"
    old = snapshot(source, output, "Old", rules_version=LEGACY_RULE_VERSION)
    manifest = output / f"{old['release_id']}.json"
    stored = json.loads(manifest.read_text())
    stored["rule_version"] = RULE_VERSION
    manifest.write_text(json.dumps(stored))
    store = ReleaseStore(output / f"{old['release_id']}.db")
    with pytest.raises(ValueError, match="content identity"):
        store.connect()
    stored["rule_version"] = "unsupported"
    manifest.write_text(json.dumps(stored))
    with pytest.raises(ValueError, match="unsupported"):
        store.connect()


def test_configured_relative_release_directory_is_resolved(tmp_path, monkeypatch):
    source = _copy_db(tmp_path)
    result = snapshot(source, tmp_path / "releases", "Candidate")
    monkeypatch.chdir(tmp_path)
    store = ReleaseStore(source, directory=Path("releases"))
    context = store.connect(result["release_id"])
    try:
        assert context.release_id == result["release_id"]
        assert store.directory == tmp_path / "releases"
    finally:
        context.connection.close()


@pytest.mark.parametrize("malformed", [None, [], {}, 1])
def test_malformed_manifest_rules_fail_as_controlled_conflicts(tmp_path, malformed):
    from fastapi.testclient import TestClient
    from findit.web.app import create_app
    source = _copy_db(tmp_path)
    output = tmp_path / "releases"
    release = snapshot(source, output, "Candidate")
    manifest = output / f"{release['release_id']}.json"
    store = ReleaseStore(source, output)
    # Start while the release is intact, then simulate a malformed manifest.
    client = TestClient(create_app(source, release_dir=output))
    value = json.loads(manifest.read_text())
    value["rule_version"] = malformed
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="unsupported"):
        store.connect(release["release_id"])
    response = client.get("/api/watchlist", params={"month": "2026-08", "stocks": "INE002A01018",
                                                 "release": release["release_id"]})
    assert response.status_code == 409
