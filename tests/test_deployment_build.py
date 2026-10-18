"""Pinned build artifacts, private downloads, and the hosted adapter boundary."""
from __future__ import annotations

import gzip
import io
import json
from pathlib import Path
import runpy
import tomllib

import pytest

from tools import prepare_vercel_release as build

ROOT = Path(__file__).resolve().parents[1]


def _fixture(tmp_path):
    source = tmp_path / "retained"
    source.mkdir()
    items = []
    for letter, rules in (("a", "readiness-2026-10-06"), ("b", "readiness-2026-10-01")):
        release = letter * 64
        database = source / (release + ".db")
        database.write_bytes(("synthetic-" + letter).encode())
        metadata = {"release_id": release, "rule_version": rules,
                    "database_sha256": build.file_sha256(database)}
        manifest = database.with_suffix(".json")
        manifest.write_text(json.dumps(metadata))
        items.append(dict(metadata, database_bytes=database.stat().st_size,
                          manifest_sha256=build.file_sha256(manifest)))
    descriptor = {"version": 1, "month": "2026-08", "current_release": "a" * 64,
                  "releases": items}
    return source, descriptor


def test_local_preparation_is_exact_and_repeatable(tmp_path):
    source, descriptor = _fixture(tmp_path)
    output = tmp_path / "deploy-data"
    build.prepare(descriptor, output, local_releases=source)
    build.prepare(descriptor, output, local_releases=source)
    # Only packed databases and manifests are bundled; no unpacked copy.
    assert sorted(path.name for path in output.iterdir()) == sorted(
        item["release_id"] + suffix for item in descriptor["releases"]
        for suffix in (".db.gz", ".json"))
    assert all(path.stat().st_mode & 0o222 == 0 for path in output.iterdir())
    assert not list(tmp_path.glob(".private-release-*"))


def test_an_unpacked_deploy_directory_from_the_earlier_format_asks_to_repack(tmp_path):
    source, descriptor = _fixture(tmp_path)
    with pytest.raises(ValueError, match="remove it and rerun to repack"):
        build.prepare(descriptor, source, local_releases=source)


def test_an_empty_deploy_directory_left_by_the_cli_upload_is_prepared(tmp_path):
    source, descriptor = _fixture(tmp_path)
    output = tmp_path / "deploy-data"
    output.mkdir()
    build.prepare(descriptor, output, local_releases=source)
    build.verify_bundle(descriptor, output)


def test_packed_releases_are_deterministic_and_unpack_to_the_pinned_bytes(tmp_path):
    source, descriptor = _fixture(tmp_path)
    first, second, unpacked = (tmp_path / name for name in ("first", "second", "unpacked"))
    for folder in (first, second, unpacked):
        folder.mkdir()
    build.pack(descriptor, source, first)
    build.pack(descriptor, source, second)
    assert {p.name: p.read_bytes() for p in first.iterdir()} == {
        p.name: p.read_bytes() for p in second.iterdir()}
    assert build.unpack(descriptor, first, unpacked) == unpacked
    build.validate_artifacts(descriptor, unpacked)
    assert all((unpacked / p.name).read_bytes() == p.read_bytes() for p in source.iterdir())
    assert all(path.stat().st_mode & 0o222 == 0 for path in unpacked.iterdir())


@pytest.mark.parametrize("damage, message", [
    ("truncated", "could not be unpacked"), ("substituted", "pinned bytes"),
    ("oversized", "exceeds its pinned byte count"), ("unpacked_copy", "only the four")])
def test_a_damaged_packed_bundle_is_never_served(tmp_path, damage, message):
    source, descriptor = _fixture(tmp_path)
    bundle, target = tmp_path / "bundle", tmp_path / "target"
    bundle.mkdir()
    target.mkdir()
    build.pack(descriptor, source, bundle)
    packed = bundle / ("a" * 64 + ".db.gz")
    if damage == "truncated":
        packed.write_bytes(packed.read_bytes()[:-6])
    elif damage == "substituted":
        packed.write_bytes(gzip.compress(b"synthetic-x"))  # same length, other bytes
    elif damage == "oversized":
        packed.write_bytes(gzip.compress(b"synthetic-a" * 100_000))
    else:
        (bundle / ("a" * 64 + ".db")).write_bytes(b"synthetic-a")
    with pytest.raises(ValueError, match=message):
        build.unpack(descriptor, bundle, target)


def test_startup_reuses_a_verified_unpacked_copy_and_replaces_a_damaged_one(tmp_path):
    source, descriptor = _fixture(tmp_path)
    bundle, scratch = tmp_path / "bundle", tmp_path / "tmp"
    bundle.mkdir()
    scratch.mkdir()
    build.pack(descriptor, source, bundle)
    served = build.materialize(descriptor, bundle, scratch)
    assert served.parent == scratch
    build.validate_artifacts(descriptor, served)
    database = served / ("a" * 64 + ".db")
    inode = database.stat().st_ino
    assert build.materialize(descriptor, bundle, scratch) == served
    assert database.stat().st_ino == inode  # reused, not unpacked again
    database.chmod(0o644)
    database.write_bytes(b"tampered!!!")
    assert build.materialize(descriptor, bundle, scratch) == served
    build.validate_artifacts(descriptor, served)
    assert [path.name for path in scratch.iterdir()] == [served.name]
    (scratch / ".findit-releases-interrupted").mkdir()
    database.chmod(0o644)
    database.write_bytes(b"tampered!!!")
    build.materialize(descriptor, bundle, scratch)
    assert [path.name for path in scratch.iterdir()] == [served.name]


@pytest.mark.parametrize("damage", ["bytes", "manifest", "extra", "symlink"])
def test_artifact_validation_rejects_corruption_and_extra_files(tmp_path, damage):
    source, descriptor = _fixture(tmp_path)
    path = source / ("a" * 64 + ".db")
    if damage == "bytes":
        path.write_bytes(b"corrupt")
    elif damage == "manifest":
        manifest = path.with_suffix(".json")
        metadata = json.loads(manifest.read_text())
        metadata["rule_version"] = "readiness-2026-10-01"
        manifest.write_text(json.dumps(metadata))
        descriptor["releases"][0]["manifest_sha256"] = build.file_sha256(manifest)
    elif damage == "extra":
        (source / "original.xlsx").write_bytes(b"private")
    else:
        copy = tmp_path / "elsewhere.db"
        path.replace(copy)
        path.symlink_to(copy)
    with pytest.raises(ValueError):
        build.validate_artifacts(descriptor, source)


def test_failed_prepare_is_atomic_and_never_public(tmp_path):
    source, descriptor = _fixture(tmp_path)
    (source / ("a" * 64 + ".db")).write_bytes(b"corrupt")
    output = tmp_path / "deploy-data"
    with pytest.raises(ValueError):
        build.prepare(descriptor, output, local_releases=source)
    assert not output.exists()
    assert not list(tmp_path.glob(".private-release-*"))
    with pytest.raises(ValueError, match="outside public"):
        build.prepare(descriptor, tmp_path / "public" / "data", local_releases=source)


def test_content_verification_checks_both_calculation_rules(tmp_path):
    import sqlite3

    from findit.store.releases import snapshot
    from tests.test_web import _copy_db

    source = _copy_db(tmp_path)
    directory = tmp_path / "releases"
    current = snapshot(source, directory, "corrected fixture")
    legacy = snapshot(source, directory, "legacy fixture", rules_version="readiness-2026-10-01")
    descriptor = {"current_release": current["release_id"],
                  "releases": [current, legacy]}
    build.validate_release_contents(descriptor, directory)
    path = directory / (legacy["release_id"] + ".db")
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE mf_holdings_monthly SET quantity=quantity+1")
    legacy["database_sha256"] = build.file_sha256(path)
    path.with_suffix(".json").write_text(json.dumps(legacy))
    with pytest.raises(ValueError, match="content identity"):
        build.validate_release_contents(descriptor, directory)


@pytest.mark.parametrize("url", ["http://store.private.blob.vercel-storage.com", "https://example.com", "https://store.public.blob.vercel-storage.com", "https://secret@store.private.blob.vercel-storage.com", "https://store.private.blob.vercel-storage.com?token=secret", "https://store.private.blob.vercel-storage.com/../data", "https://store.private.blob.vercel-storage.com:443/data"])
def test_private_artifact_origin_is_restricted(url):
    with pytest.raises(ValueError):
        build.private_base_url(url)


def test_authenticated_download_is_bounded_and_does_not_log_credentials(tmp_path):
    source, descriptor = _fixture(tmp_path)
    requests = []

    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert timeout == 30
            assert request.get_header("Authorization") == "Bearer test-secret"
            assert "test-secret" not in request.full_url
            return Response((source / request.full_url.rsplit("/", 1)[1]).read_bytes())

    build.prepare(descriptor, tmp_path / "deploy-data", opener=Opener(),
                  base_url="https://store.private.blob.vercel-storage.com/releases",
                  token="test-secret")
    assert len(requests) == 4

    class Failure:
        def open(self, request, timeout):
            raise OSError("provider included test-secret in an error")

    with pytest.raises(ValueError) as caught:
        build._download(Failure(), "https://private.invalid", "test-secret", tmp_path / "bad", 1)
    assert "test-secret" not in str(caught.value)
    with pytest.raises(ValueError):
        build._download(Opener(), requests[0].full_url, "test-secret", tmp_path / "big", 1)
    with pytest.raises(ValueError, match="redirects"):
        build._RejectRedirect().redirect_request(None, None, 302, "", {}, "https://attacker")


@pytest.mark.parametrize("field,value", [("version", 2), ("version", True), ("month", "2026-99"), ("current_release", "f" * 64), ("releases", [])])
def test_descriptor_rejects_unpinned_inputs(tmp_path, field, value):
    _, descriptor = _fixture(tmp_path)
    descriptor[field] = value
    path = tmp_path / "descriptor.json"
    path.write_text(json.dumps(descriptor))
    with pytest.raises(ValueError):
        build.load_descriptor(path)


def test_bundle_gate_and_server_only_inventory(tmp_path, monkeypatch):
    source, _ = _fixture(tmp_path)
    (tmp_path / "app.py").write_text("app = None")
    private = tmp_path / "real_data"
    private.mkdir()
    (private / "original.xlsx").write_bytes(b"source disclosure")
    inventory = build.bundle_inventory(tmp_path, source, dependency_roots=[])
    assert "real_data/original.xlsx" not in inventory["source_files"]
    assert inventory["bundle_bytes"] > 0
    monkeypatch.setattr(build, "BUNDLE_BUDGET", 1)
    with pytest.raises(ValueError, match="safety budget"):
        build.bundle_inventory(tmp_path, source, dependency_roots=[])


def test_adapter_requires_exact_hosts_and_pins_factory_arguments(tmp_path, monkeypatch):
    import findit.web.app as web

    source, descriptor = _fixture(tmp_path)
    output = tmp_path / "deploy-data"
    build.prepare(descriptor, output, local_releases=source)
    (tmp_path / "deployment").mkdir()
    (tmp_path / "deployment" / "releases.json").write_text(json.dumps(descriptor))
    calls = []
    monkeypatch.setattr(web, "create_app", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(build, "load_descriptor", lambda path: descriptor)
    unpacked = tmp_path / "unpacked"
    materialized = []
    monkeypatch.setattr(build, "materialize",
                        lambda data, bundle, scratch: materialized.append(bundle) or unpacked)
    monkeypatch.setenv("VERCEL_URL", "findit-test.vercel.app")
    monkeypatch.setenv("VERCEL_BRANCH_URL", "findit-branch.vercel.app")
    wrapper = runpy.run_path(str(ROOT / "app.py"))
    wrapper["hosted_app"](tmp_path)
    args, kwargs = calls[-1]
    assert materialized[-1] == output  # import-time app() used the checkout first
    assert args == (unpacked / ("a" * 64 + ".db"), unpacked)
    assert kwargs == {"allowed_hosts": ("findit-test.vercel.app", "findit-branch.vercel.app"),
                      "hosted": True, "expected_month": "2026-08"}
    monkeypatch.delenv("VERCEL_URL")
    monkeypatch.delenv("VERCEL_BRANCH_URL")
    with pytest.raises(ValueError, match="required"):
        wrapper["hosted_app"](tmp_path)
    monkeypatch.setenv("VERCEL_URL", "*.vercel.app")
    with pytest.raises(ValueError, match="exact deployment"):
        wrapper["hosted_app"](tmp_path)
    monkeypatch.setenv("VERCEL_ENV", "production")
    with pytest.raises(ValueError, match="protected previews"):
        wrapper["hosted_app"](tmp_path)


def test_descriptor_rejects_releases_too_large_to_unpack(tmp_path, monkeypatch):
    _, descriptor = _fixture(tmp_path)
    path = tmp_path / "descriptor.json"
    path.write_text(json.dumps(descriptor))
    monkeypatch.setattr(build, "UNPACKED_BUDGET", 21)  # the fixture holds 22 bytes
    with pytest.raises(ValueError, match="temporary-storage safety budget"):
        build.load_descriptor(path)


def test_descriptor_rejects_unhashable_rule_version(tmp_path):
    _, descriptor = _fixture(tmp_path)
    descriptor["releases"][0]["rule_version"] = []
    path = tmp_path / "descriptor.json"
    path.write_text(json.dumps(descriptor))
    with pytest.raises(ValueError, match="calculation rules"):
        build.load_descriptor(path)


def test_deployment_config_preserves_assets_and_excludes_private_sources():
    config = json.loads((ROOT / "vercel.json").read_text())
    settings = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert settings["tool"]["vercel"]["entrypoint"] == "app:app"
    assert settings["tool"]["vercel"]["scripts"]["build"] == "python tools/prepare_vercel_release.py"
    assert settings["tool"]["vercel"]["fastapi"]["static"]["exclude"] is False
    function = config["functions"]["app.py"]
    bundled = set(function["includeFiles"].strip("{}").split(","))
    assert "deploy-data/*.db.gz" in bundled and "deploy-data/*.db" not in bundled
    assert all(pattern in function["excludeFiles"] for pattern in ("real_data/**", "outputs/**", ".venv/**", ".env.*"))
    assert config["public"] is False
    assert "deploy-data/" in (ROOT / ".vercelignore").read_text().splitlines()
