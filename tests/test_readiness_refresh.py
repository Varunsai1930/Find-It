import json
import subprocess

import pytest

from findit.cli.refresh import refresh
from findit.store.releases import snapshot
from tests.test_web import _copy_db


def test_staging_repeatability_and_failed_refresh(tmp_path):
    source = _copy_db(tmp_path)
    original = source.read_bytes()
    releases = tmp_path / "releases"
    good = snapshot(source, releases, "baseline")
    pointer = (releases / "current.json").read_bytes()
    a = refresh(source, tmp_path / "a.db", "2026-07", "2026-08", [], [])
    b = refresh(source, tmp_path / "b.db", "2026-07", "2026-08", [], [])
    assert source.read_bytes() == original
    assert a["release_id"] == b["release_id"]
    repeated = refresh(tmp_path / "a.db", tmp_path / "repeated.db", "2026-07", "2026-08", [], [])
    assert repeated["release_id"] == a["release_id"]
    assert good["release_id"] == json.loads((releases / "current.json").read_text())["release_id"]
    assert a["status"] == "staged_review_required" and a["processing_seconds"] > 0
    bad = tmp_path / "bad.csv"
    bad.write_text("invalid,csv\n1,2\n")
    with pytest.raises(subprocess.CalledProcessError):
        refresh(source, tmp_path / "failed.db", "2026-07", "2026-08", [bad], [], releases, "review")
    assert json.loads((tmp_path / "failed.refresh.json").read_text())["status"] == "failed"
    assert (releases / "current.json").read_bytes() == pointer
    assert source.read_bytes() == original
    with pytest.raises(ValueError, match="separate"):
        refresh(source, source, "2026-07", "2026-08", [], [])
    with pytest.raises(ValueError, match="review notes"):
        refresh(source, tmp_path / "no-review.db", "2026-07", "2026-08", [], [], releases)
