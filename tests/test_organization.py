from __future__ import annotations

CURRENT_LEAVE_QUESTION = "员工入职满三年，年假有多少天？"


def test_organization_status_exposes_agent_team(seeded_client):
    response = seeded_client.get("/api/v1/organization")

    assert response.status_code == 200
    status = response.json()
    names = {agent["name"] for agent in status["agents"]}
    assert names == {"coordinator", "retriever", "reasoner", "auditor", "experience"}
    assert status["experience_total"] == 0
    assert status["experience_reuse_enabled"] is True
    assert len(status["loop"]) >= 4


def test_multi_hop_question_compares_versions(seeded_client):
    response = seeded_client.post(
        "/api/v1/chat",
        json={"question": "2025年入职满5年的年假，和2026年新制度的区别？"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["refused"] is False
    assert payload["plan_strategy"] == "temporal_comparison"
    assert "12 天" in payload["answer"]
    assert "15 天" in payload["answer"]
    versions = {citation["version"] for citation in payload["citations"]}
    assert {"2025", "2026"}.issubset(versions)
    agent_names = [agent["agent"] for agent in payload["agents"]]
    assert {"coordinator", "retriever", "reasoner", "auditor"}.issubset(agent_names)
    assert payload["quality_score"] >= 0.9
    assert payload["experience_ids"] == []


def test_audited_answer_becomes_reusable_experience(seeded_client):
    first = seeded_client.post(
        "/api/v1/chat",
        json={"question": CURRENT_LEAVE_QUESTION, "as_of": "2026-06-01"},
    ).json()
    assert first["experience_reused"] is False
    assert first["experience_ids"]

    second = seeded_client.post(
        "/api/v1/chat",
        json={"question": CURRENT_LEAVE_QUESTION, "as_of": "2026-06-01"},
    ).json()

    assert second["experience_reused"] is True
    assert second["plan_strategy"] == "experience_reuse"
    assert second["experience_ids"] == first["experience_ids"]
    assert "10 天" in second["answer"]
    assert "experience" in {agent["agent"] for agent in second["agents"]}


def test_feedback_can_suppress_a_bad_experience(seeded_client):
    first = seeded_client.post(
        "/api/v1/chat",
        json={"question": CURRENT_LEAVE_QUESTION, "as_of": "2026-06-01"},
    ).json()
    run_id = first["run_id"]

    feedback = seeded_client.post(
        f"/api/v1/runs/{run_id}/feedback",
        json={"rating": 2, "comment": "答案不适用"},
    )
    assert feedback.status_code == 200
    assert feedback.json()["action"] == "suppressed"

    replay = seeded_client.post(
        "/api/v1/chat",
        json={"question": CURRENT_LEAVE_QUESTION, "as_of": "2026-06-01"},
    ).json()
    assert replay["experience_reused"] is False


def test_feedback_promotes_a_high_quality_answer(seeded_client):
    first = seeded_client.post(
        "/api/v1/chat",
        json={"question": CURRENT_LEAVE_QUESTION, "as_of": "2026-06-01"},
    ).json()

    feedback = seeded_client.post(
        f"/api/v1/runs/{first['run_id']}/feedback",
        json={"rating": 5, "comment": "引用和时效都正确"},
    )

    assert feedback.status_code == 200
    payload = feedback.json()
    assert payload["action"] in {"promoted", "recorded"}
    assert payload["experience_id"]


def test_chinese_numeral_query_uses_historical_version(seeded_client):
    response = seeded_client.post(
        "/api/v1/chat",
        json={"question": "三年年假多少天？", "as_of": "2025-06-01"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["refused"] is False
    assert "8 天" in payload["answer"]
    assert payload["citations"][0]["source"] == "employee-handbook-2025.md"


def test_evolution_endpoint_proposes_without_mutating_by_default(seeded_client):
    response = seeded_client.post(
        "/api/v1/organization/evolve",
        json={"apply": False},
    )

    assert response.status_code == 200
    report = response.json()
    assert report["baseline"]["min_query_coverage"] == 0.30
    assert len(report["candidates"]) >= 4
    assert report["best"]["pass_rate"] == 1.0
    assert report["applied"] is False
    assert report["applied_value"] is None
    assert "same fixed benchmark" in report["caveat"]
