from __future__ import annotations

from app.knowledge.loader import load_bytes


def test_frontmatter_is_parsed_and_removed_from_content():
    raw = (
        "---\n"
        "policy_key: leave-policy\n"
        'version: "2026"\n'
        "effective_from: 2026-01-01\n"
        "---\n"
        "# 请假制度\n\n"
        "年假为 10 天。\n"
    ).encode()

    loaded = load_bytes("leave-policy.md", raw, content_type="text/markdown")

    assert loaded.title == "请假制度"
    assert loaded.metadata["policy_key"] == "leave-policy"
    assert loaded.metadata["effective_from"] == "2026-01-01"
    assert loaded.content.startswith("# 请假制度")
    assert "policy_key" not in loaded.content


def test_as_of_switches_policy_versions(seeded_client):
    current = seeded_client.post(
        "/api/v1/chat",
        json={"question": "员工入职满三年，年假有多少天？", "as_of": "2026-06-01"},
    )
    assert current.status_code == 200
    current_payload = current.json()
    assert current_payload["as_of"] == "2026-06-01"
    assert "10 天" in current_payload["answer"]
    assert current_payload["citations"][0]["source"] == "employee-handbook.md"
    assert current_payload["citations"][0]["version"] == "2026"

    historical = seeded_client.post(
        "/api/v1/chat",
        json={"question": "员工入职满三年，年假有多少天？", "as_of": "2025-06-01"},
    )
    assert historical.status_code == 200
    historical_payload = historical.json()
    assert historical_payload["as_of"] == "2025-06-01"
    assert "8 天" in historical_payload["answer"]
    assert historical_payload["citations"][0]["source"] == "employee-handbook-2025.md"
    assert historical_payload["citations"][0]["version"] == "2025"
    assert historical_payload["citations"][0]["effective_until"] == "2025-12-31"


def test_document_listing_can_filter_by_as_of(seeded_client):
    response = seeded_client.get("/api/v1/documents?as_of=2025-06-01&limit=100")

    assert response.status_code == 200
    payload = response.json()
    sources = {item["source"] for item in payload["items"]}
    assert "employee-handbook-2025.md" in sources
    assert "employee-handbook.md" not in sources
    assert all(
        item["effective_until"] is None or item["effective_until"] >= "2025-06-01"
        for item in payload["items"]
    )


def test_overlapping_policy_window_is_rejected(seeded_client):
    response = seeded_client.post(
        "/api/v1/documents",
        json={
            "title": "员工手册 2026 补丁",
            "source": "employee-handbook-hotfix.md",
            "content": "# 员工手册补丁\n\n这是一份用于测试版本冲突的补丁制度，年假仍为 10 天。",
            "policy_key": "employee-handbook",
            "version": "2026-hotfix",
            "effective_from": "2026-06-01",
            "effective_until": "2026-12-31",
        },
    )

    assert response.status_code == 409
    assert "overlaps" in response.json()["detail"]["message"]


def test_policy_key_requires_effective_date(seeded_client):
    response = seeded_client.post(
        "/api/v1/documents",
        json={
            "title": "缺少生效日期的制度",
            "source": "missing-effective-date.md",
            "content": "# 缺少生效日期\n\n这份制度没有生效日期。",
            "policy_key": "employee-handbook",
        },
    )

    assert response.status_code == 422
