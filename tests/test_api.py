

def test_demo_mode_runs_offline_with_local_search(monkeypatch):
    from fastapi.testclient import TestClient

    from meridian.api.server import create_app
    from meridian.config import Settings

    monkeypatch.setenv("MERIDIAN_DEMO", "1")
    with TestClient(create_app(Settings())) as c:
        h = c.get("/api/health").json()
    assert h["model"] == "offline scripted demo" and h["search"] == "local"
