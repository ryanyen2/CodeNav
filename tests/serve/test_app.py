"""U1 — the hub HTTP app skeleton: health endpoint + SPA catch-all."""
from __future__ import annotations

from fastapi.testclient import TestClient

from codoc.serve.app import build_app


def test_healthz_ok(tmp_path):
    client = TestClient(build_app(str(tmp_path)))
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["service"] == "codoc-serve"


def test_catch_all_serves_placeholder_when_no_spa(tmp_path):
    client = TestClient(build_app(str(tmp_path)))
    r = client.get("/some/deep/spa/route")
    assert r.status_code == 200
    assert "codoc serve" in r.text


def test_catch_all_serves_index_when_spa_present(tmp_path):
    spa = tmp_path / "web"
    spa.mkdir()
    (spa / "index.html").write_text("<html><body>EDITOR BUNDLE</body></html>")
    client = TestClient(build_app(str(tmp_path), static_dir=str(spa)))
    r = client.get("/anything")
    assert r.status_code == 200
    assert "EDITOR BUNDLE" in r.text


def test_healthz_not_shadowed_by_catch_all(tmp_path):
    spa = tmp_path / "web"
    spa.mkdir()
    (spa / "index.html").write_text("INDEX")
    client = TestClient(build_app(str(tmp_path), static_dir=str(spa)))
    assert client.get("/healthz").json()["ok"] is True
