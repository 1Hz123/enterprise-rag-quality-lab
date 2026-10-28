from __future__ import annotations

RUN_DOCUMENT = {
    "title": "测试工单 SLA",
    "source": "runs-sla.md",
    "content": (
        "# 测试工单 SLA\n\n"
        "P1 级客户工单要求 15 分钟内首次响应、4 小时内恢复。"
        "P2 级工单要求 1 小时内响应、1 个工作日内恢复。"
    ),
}


def _create_run(client) -> str:
    assert client.post("/api/v1/documents", json=RUN_DOCUMENT).status_code == 201
    response = client.post(
        "/api/v1/chat",
        json={"question": "客户 P1 工单的响应和恢复时限是多少？"},
    )
    assert response.status_code == 200
    return response.json()["run_id"]


def test_runs_are_listed_and_retrievable(client):
    run_id = _create_run(client)

    listing = client.get("/api/v1/runs?limit=10&offset=0")
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["total"] == 1
    assert payload["items"][0]["run_id"] == run_id
    assert payload["items"][0]["citation_count"] >= 1

    detail = client.get(f"/api/v1/runs/{run_id}")
    assert detail.status_code == 200
    run = detail.json()
    assert run["question"] == "客户 P1 工单的响应和恢复时限是多少？"
    assert run["retrieval"]
    assert run["citations"]
    assert run["metrics"]["citation_count"] >= 1
    assert run["error"] is None


def test_run_not_found(client):
    response = client.get("/api/v1/runs/run_missing")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_run_list_pagination_is_bounded(client):
    _create_run(client)

    assert client.get("/api/v1/runs?limit=0").status_code == 422
    assert client.get("/api/v1/runs?limit=101").status_code == 422
    assert client.get("/api/v1/runs?offset=-1").status_code == 422
