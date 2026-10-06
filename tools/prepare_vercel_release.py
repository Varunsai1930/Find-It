"""Prepare exactly two pinned private releases for a protected Vercel build.

Downloads run at build time only. Tokens never become artifact contents or
request parameters, and redirects are rejected before credentials can escape.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import sys
import sysconfig
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_BUDGET = 400_000_000
MANIFEST_BUDGET = 64_000
_HEX = re.compile(r"[a-f0-9]{64}")
_RULES = {"readiness-2026-10-01", "readiness-2026-10-06"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1_048_576), b""):
            digest.update(block)
    return digest.hexdigest()


def load_descriptor(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or type(data.get("version")) is not int or
            data.get("version") != 1 or
            not isinstance(data.get("month"), str) or
            not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", data["month"]) or
            not isinstance(data.get("releases"), list) or len(data["releases"]) != 2):
        raise ValueError("Deployment descriptor must pin a month and exactly two releases")
    ids = []
    for item in data["releases"]:
        if not isinstance(item, dict):
            raise ValueError("Invalid release descriptor")
        for key in ("release_id", "database_sha256", "manifest_sha256"):
            if not isinstance(item.get(key), str) or not _HEX.fullmatch(item[key]):
                raise ValueError("Release IDs and hashes must be complete SHA-256 values")
        if (type(item.get("database_bytes")) is not int or
                not 0 < item["database_bytes"] < BUNDLE_BUDGET or
                not isinstance(item.get("rule_version"), str) or
                item.get("rule_version") not in _RULES):
            raise ValueError("Invalid release byte count or calculation rules")
        ids.append(item["release_id"])
    if len(set(ids)) != 2 or data.get("current_release") not in ids:
        raise ValueError("Current release must be one of two distinct pinned releases")
    current = next(item for item in data["releases"]
                   if item["release_id"] == data["current_release"])
    if current["rule_version"] != "readiness-2026-10-06":
        raise ValueError("The current deployment must use corrected calculation rules")
    if {item["rule_version"] for item in data["releases"]} != _RULES:
        raise ValueError("Pin the corrected release and its legacy replay release")
    return data


def validate_artifacts(descriptor: dict, directory: Path) -> None:
    expected = {item["release_id"] + suffix for item in descriptor["releases"]
                for suffix in (".db", ".json")}
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Prepared private release directory is unavailable")
    if {path.name for path in directory.iterdir()} != expected:
        raise ValueError("Prepared directory must contain only the four pinned release files")
    for item in descriptor["releases"]:
        database = directory / (item["release_id"] + ".db")
        manifest = database.with_suffix(".json")
        if any(path.is_symlink() or not path.is_file() for path in (database, manifest)):
            raise ValueError("Release artifacts must be ordinary files")
        if (database.stat().st_size != item["database_bytes"] or
                manifest.stat().st_size > MANIFEST_BUDGET or
                file_sha256(database) != item["database_sha256"] or
                file_sha256(manifest) != item["manifest_sha256"]):
            raise ValueError("Prepared release does not match its pinned bytes")
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or any(metadata.get(key) != item[key]
               for key in ("release_id", "rule_version", "database_sha256")):
            raise ValueError("Release manifest does not match the deployment descriptor")


def validate_release_contents(descriptor: dict, directory: Path) -> None:
    """Verify both SQLite content identities with the application's replay rules."""
    from findit.store.releases import ReleaseStore

    current = directory / (descriptor["current_release"] + ".db")
    store = ReleaseStore(current, directory)
    for item in descriptor["releases"]:
        store.retained(item["release_id"])


class _RejectRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Private artifact redirects are not allowed")


def private_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None or
            parsed.port or parsed.query or parsed.fragment or
            not re.fullmatch(r"[a-zA-Z0-9-]+\.private\.blob\.vercel-storage\.com",
                             parsed.hostname or "") or ".." in parsed.path.split("/")):
        raise ValueError("Configure an HTTPS private Vercel Blob directory")
    return value.rstrip("/")


def _download(opener, url: str, token: str, path: Path, maximum: int) -> None:
    request = Request(url, headers={"Authorization": "Bearer " + token})
    total = 0
    try:
        with opener.open(request, timeout=30) as response, path.open("wb") as stream:
            if response.status != 200:
                raise ValueError("Private artifact download failed")
            while block := response.read(1_048_576):
                total += len(block)
                if total > maximum:
                    raise ValueError("Private artifact exceeds its pinned byte budget")
                stream.write(block)
    except Exception:
        # Network exceptions may include URLs or other provider details.
        # Preserve a useful failure without placing credentials in build logs.
        raise ValueError("Private artifact download failed or exceeded its byte budget") from None


def prepare(descriptor: dict, output: Path, *, local_releases: Path | None = None,
            base_url: str | None = None, token: str | None = None, opener=None) -> None:
    if "public" in output.resolve().parts:
        raise ValueError("Release files must stay outside public asset directories")
    if output.exists():
        validate_artifacts(descriptor, output)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    if local_releases is None:
        base_url = private_base_url(base_url or "")
        if not token or any(char in token for char in "\r\n"):
            raise ValueError("A private artifact credential is required at build time")
        opener = opener or build_opener(_RejectRedirect(),
                                         HTTPSHandler(context=ssl.create_default_context()))
    with tempfile.TemporaryDirectory(prefix=".private-release-", dir=output.parent) as tmp:
        staged = Path(tmp) / "deploy-data"
        staged.mkdir()
        for item in descriptor["releases"]:
            for suffix in (".db", ".json"):
                name = item["release_id"] + suffix
                target = staged / name
                if local_releases is not None:
                    source = local_releases / name
                    if source.is_symlink():
                        raise ValueError("Release inputs cannot be symbolic links")
                    shutil.copyfile(source, target)
                else:
                    maximum = item["database_bytes"] if suffix == ".db" else MANIFEST_BUDGET
                    _download(opener, base_url + "/" + name, token, target, maximum)
        validate_artifacts(descriptor, staged)
        for path in staged.iterdir():
            path.chmod(0o444)
        staged.replace(output)


def bundle_inventory(root: Path, data_dir: Path, *, dependency_roots=None) -> dict:
    """Conservatively count runtime source, data and installed dependencies."""
    source_paths = [root / name for name in ("app.py", "pyproject.toml", "uv.lock",
                    "deployment/releases.json", "tools/prepare_vercel_release.py")]
    source_paths.extend(path for path in (root / "findit").rglob("*") if path.is_file())
    source_paths.extend(data_dir.iterdir())
    roots = dependency_roots if dependency_roots is not None else {
        Path(sysconfig.get_paths()[key]) for key in ("purelib", "platlib")}
    dependencies = {path.resolve() for folder in roots for path in folder.rglob("*")
                    if path.is_file()}
    sources = {path.resolve() for path in source_paths if path.is_file()}
    # Source duplicated inside an installed package is counted conservatively.
    total = sum(path.stat().st_size for path in sources) + sum(
        path.stat().st_size for path in dependencies)
    if total > BUNDLE_BUDGET:
        raise ValueError("Prepared runtime exceeds the 400 MB deployment safety budget")
    return {"bundle_bytes": total, "budget_bytes": BUNDLE_BUDGET,
            "source_files": sorted(str(path.relative_to(root.resolve())) for path in sources
                                   if path.is_relative_to(root.resolve())),
            "dependency_files": len(dependencies)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor", type=Path, default=ROOT / "deployment/releases.json")
    parser.add_argument("--output", type=Path, default=ROOT / "deploy-data")
    parser.add_argument("--local-releases", type=Path,
                        help="Verify and copy a local retained pair instead of downloading")
    parser.add_argument("--inventory-output", type=Path, default=ROOT / ".vercel-build-audit.json")
    args = parser.parse_args(argv)
    descriptor = load_descriptor(args.descriptor)
    prepare(descriptor, args.output, local_releases=args.local_releases,
            base_url=os.environ.get("FINDIT_BLOB_BASE_URL"),
            token=os.environ.get("BLOB_READ_WRITE_TOKEN"))
    # Direct script execution sets sys.path to tools/, while the Vercel build
    # must verify the application source being bundled, not another install.
    sys.path.insert(0, str(ROOT))
    validate_release_contents(descriptor, args.output)
    inventory = bundle_inventory(ROOT, args.output)
    args.inventory_output.write_text(json.dumps(inventory, indent=2) + "\n")
    print("Verified two private releases; runtime bundle budget passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        raise SystemExit("Deployment preparation failed: " + str(exc)) from None
