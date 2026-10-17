"""Behaviour that differs on Windows, checked on every platform."""

import os
import subprocess
import sys
from pathlib import Path

from findit.store import db

ROOT = Path(__file__).resolve().parents[1]


def test_read_only_connection_opens_paths_with_spaces_and_hashes(tmp_path):
    """A hand-built "file:{path}" URI stopped at '#', opening an empty database."""
    path = tmp_path / "Fund data #2" / "tracker.db"
    path.parent.mkdir()
    db.get_connection(str(path)).close()
    conn = db.connect_read_only(path)
    try:
        assert conn.execute("SELECT count(*) FROM schemes").fetchone() == (0,)
    finally:
        conn.close()


def test_cli_output_survives_a_code_page_without_rupee_or_arrow():
    """Redirected output on Windows uses the system code page (cp1252 here)."""
    env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONPATH=str(ROOT))
    result = subprocess.run([sys.executable, "-c", "import findit.cli; print('₹ 5 cr → 6 cr')"],
                            capture_output=True, env=env, cwd=ROOT)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout.decode("utf-8").strip() == "₹ 5 cr → 6 cr"
