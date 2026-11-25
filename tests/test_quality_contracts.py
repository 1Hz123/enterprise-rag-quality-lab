from __future__ import annotations

from datetime import date

from app.evaluation.dataset import load_dataset
from app.knowledge.loader import LoadedDocument
from app.rag.generator import Generation

CURRENT_LEAVE_QUESTION = "员工入职满三年，年假有多少天？"


def test_raw_invalid_citation_is_audited_and_removed(seeded_client):
    pipeline = seeded_client.app.state.pipeline

    class InvalidCitationGenerator:
        mode = "invalid-test"

        def generate(self, question, items):
            return Generation(text="员工年假为 15 天 [99]。", used_indices=[1])

    pipeline.generator = InvalidCitationGenerator()
    result = pipeline.answer(CURRENT_LEAVE_QUESTION, as_of=date(2026, 6, 1))

    assert "[99]" not in result.answer
    assert "[99]" in result.raw_answer
    assert result.invalid_citation_count == 1
    assert result.invalid_citation_rate == 1.0
    assert result.metrics["invalid_citation_rate"] == 1.0


def test_invalid_raw_citation_rate_fails_quality_gate(seeded_client):
    pipeline = seeded_client.app.state.pipeline

    class InvalidCitationGenerator:
        mode = "invalid-test"

        def generate(self, question, items):
            return Generation(text="员工年假为 15 天 [99]。", used_indices=[1])

    pipeline.generator = InvalidCitationGenerator()
    dataset = load_dataset(seeded_client.app.state.settings.eval_dataset_path)
    report = seeded_client.app.state.evaluator.evaluate(dataset, name="invalid-citation")

    assert report.metrics.invalid_citation_rate > 0
    assert report.quality_gate.checks["invalid_citation_rate"] is False
    assert report.quality_gate.passed is False


def test_citation_contains_verified_sentence_quote(seeded_client):
    response = seeded_client.post(
        "/api/v1/chat",
        json={"question": CURRENT_LEAVE_QUESTION, "as_of": "2026-06-01"},
    ).json()

    citation = response["citations"][0]
    assert citation["quote"]
    assert citation["quote_verified"] is True
    assert citation["quote"] in citation["snippet"] or citation["quote"][:20] in citation["snippet"]


def test_policy_title_without_matching_document_refuses(seeded_client):
    question = "《国务院办公厅关于加强中小企业回款难问题治理有关工作的通知》主要部署了哪些任务？"
    response = seeded_client.post(
        "/api/v1/chat",
        json={"question": question, "as_of": "2026-06-01"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["refused"] is True
    assert payload["citations"] == []
    assert payload["experience_ids"] == []
    assert "中小学生" not in payload["answer"]
    assert "睡眠" not in payload["answer"]


def test_generic_leave_question_asks_for_clarification(seeded_client):
    response = seeded_client.post(
        "/api/v1/chat",
        json={"question": "员工的假期是多少天", "as_of": "2026-06-01"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["refused"] is True
    assert payload["citations"] == []
    assert payload["experience_ids"] == []
    assert "年假" in payload["answer"]
    assert "病假" in payload["answer"]


def test_experience_can_be_reused_across_days_for_open_policy_window(seeded_client):
    first = seeded_client.post(
        "/api/v1/chat",
        json={"question": CURRENT_LEAVE_QUESTION, "as_of": "2026-06-01"},
    ).json()
    assert first["experience_ids"]

    second = seeded_client.post(
        "/api/v1/chat",
        json={"question": CURRENT_LEAVE_QUESTION, "as_of": "2026-06-02"},
    ).json()

    assert second["experience_reused"] is True
    assert second["experience_ids"] == first["experience_ids"]


def test_document_versions_and_same_content_different_window(client):
    base = {
        "title": "年假对照制度",
        "content": "# 年假对照制度\n\n员工年假为 10 天。",
        "policy_key": "leave-compare",
        "version": "2024",
        "effective_from": "2024-01-01",
        "effective_until": "2024-12-31",
    }
    first = client.post("/api/v1/documents", json=base)
    assert first.status_code == 201
    document_id = first.json()["id"]
    assert first.json()["fingerprint"]

    versions = client.get(f"/api/v1/documents/{document_id}/versions")
    assert versions.status_code == 200
    assert versions.json()["total"] == 1

    second = client.post(
        "/api/v1/documents",
        json={
            **base,
            "version": "2025",
            "effective_from": "2025-01-01",
            "effective_until": "2025-12-31",
        },
    )
    assert second.status_code == 201
    assert second.json()["id"] != document_id


def test_update_snapshots_previous_document_version(client):
    created = client.post(
        "/api/v1/documents",
        json={
            "title": "版本快照制度",
            "source": "snapshot-policy.md",
            "content": "# 版本快照制度\n\n旧版年假为 5 天。",
        },
    ).json()

    updated = client.app.state.document_service.update_from_loaded(
        created["id"],
        LoadedDocument(
            title="版本快照制度",
            source="snapshot-policy.md",
            content="# 版本快照制度\n\n新版年假为 10 天。",
        ),
    )
    assert updated.content.endswith("新版年假为 10 天。")

    versions = client.get(f"/api/v1/documents/{created['id']}/versions").json()
    assert versions["total"] == 2
    assert {item["checksum"] for item in versions["items"]} == {
        created["checksum"],
        updated.checksum,
    }
