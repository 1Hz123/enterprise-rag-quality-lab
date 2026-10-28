from __future__ import annotations


def test_root_exposes_entry_points(client):
    response = client.get("/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["docs"] == "/docs"
    assert payload["api"]["chat"] == "/api/v1/chat"


def test_health_reports_database_and_configuration(client):
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"] == "ok"
    assert payload["environment"] == "test"
    assert payload["documents"] == 0
    assert payload["chunks"] == 0
    assert payload["generator"] == "extractive"
    assert payload["embedding"] == "hashing-384"


def test_health_counts_ingested_documents(client):
    response = client.post(
        "/api/v1/documents",
        json={
            "title": "健康检查文档",
            "source": "health-check.md",
            "content": "# 健康检查\n\n这是一个用于健康检查的短文档。",
        },
    )
    assert response.status_code == 201

    health = client.get("/health").json()
    assert health["documents"] == 1
    assert health["chunks"] >= 1
