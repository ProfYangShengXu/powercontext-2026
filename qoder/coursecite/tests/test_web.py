from __future__ import annotations

import json

import pytest
from conftest import SNAPSHOT
from fastapi.testclient import TestClient

from coursecite.bootstrap.main import build_app
from coursecite.config import Settings
from coursecite.web import create_app


@pytest.fixture()
def client() -> TestClient:
    settings = Settings.from_env(
        {"COURSECITE_STORE": "snapshot", "COURSECITE_SNAPSHOT": str(SNAPSHOT)}
    )
    app = build_app(settings)
    return TestClient(create_app(app.search, settings))


def test_meta_exposes_snapshot_mode_and_suggestions(client: TestClient):
    data = client.get("/api/meta").json()
    assert data["store_mode"] == "snapshot"
    assert len(data["suggestions"]) == 5
    assert data["stats"]["chunks"] == 66


def test_search_returns_pre_sorted_hits(client: TestClient):
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    entry = payload["queries"][0]
    response = client.get("/api/search", params={"q": entry["query"]})
    assert response.status_code == 200
    body = response.json()
    assert [hit["chunk_id"] for hit in body["hits"]] == [row["chunk_id"] for row in entry["results"]]
    assert [hit["rank"] for hit in body["hits"]] == list(range(1, len(entry["results"]) + 1))


def test_unknown_query_returns_404_with_available(client: TestClient):
    response = client.get("/api/search", params={"q": "快照里没有的问题"})
    assert response.status_code == 404
    body = response.json()
    assert "离线快照中没有这个问题" in body["error"]
    assert len(body["available"]) == 5


def test_obtain_returns_full_paragraph(client: TestClient):
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    chunk_id = payload["queries"][0]["results"][0]["chunk_id"]
    response = client.get(f"/api/obtain/{chunk_id}")
    assert response.status_code == 200
    assert response.json()["text"]


def test_healthz(client: TestClient):
    assert client.get("/healthz").json()["ok"] is True
