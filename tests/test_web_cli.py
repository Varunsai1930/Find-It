"""The launch command uses the selected DB and stays local by default."""
import pytest
from fastapi.testclient import TestClient

from findit.cli import web
from findit.store import db


def test_launch_uses_selected_database_and_local_address(tmp_path, monkeypatch):
    path = tmp_path / "demo.db"
    db.get_connection(str(path)).close()
    started = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: started.append((app, kw)))
    assert web.main(["--db", str(path), "--port", "8123"]) == 0
    app, options = started[0]
    assert options == {"host": "127.0.0.1", "port": 8123}
    assert TestClient(app).get("/api/coverage").status_code == 200


def test_launch_respects_database_environment(tmp_path, monkeypatch):
    path = tmp_path / "env.db"
    db.get_connection(str(path)).close()
    monkeypatch.setenv("FINDIT_DB", str(path))
    started = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: started.append(app))
    assert web.main([]) == 0
    assert TestClient(started[0]).get("/api/coverage").status_code == 200


@pytest.mark.parametrize("port", ["0", "65536"])
def test_launch_rejects_invalid_ports(port):
    with pytest.raises(SystemExit) as exc:
        web.main(["--port", port])
    assert exc.value.code == 2
