from __future__ import annotations

import math

from app.config import Settings
from app.db import Database
from app.evaluation.dataset import load_dataset
from app.knowledge.embeddings import HashingEmbeddings, build_embeddings, cosine_similarity
from app.knowledge.retriever import Retriever
from app.knowledge.splitter import split_text


def test_splitter_respects_budget_and_sections():
    text = "# 制度\n\n" + "\n\n".join(
        f"## 第 {index} 节\n\n这一节说明第 {index} 项流程。员工应提交申请并获得审批。"
        for index in range(1, 12)
    )

    chunks = split_text(text, chunk_size=180, overlap=30)

    assert len(chunks) > 1
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert all(len(chunk.text) <= 180 for chunk in chunks)
    assert any(chunk.section and "第 1 节" in chunk.section for chunk in chunks)
    assert all(chunk.text.strip() for chunk in chunks)


def test_empty_text_produces_no_chunks():
    assert split_text("   \n\n ") == []


def test_hashing_embeddings_prefer_related_text():
    embeddings = HashingEmbeddings(dimension=384)
    query = embeddings.embed_query("VPN 申请审批后多久开通")
    related = embeddings.embed_query("员工提交 VPN 申请，审批后 1 个工作日内开通。")
    unrelated = embeddings.embed_query("一线城市住宿标准不超过 600 元每晚。")

    related_score = cosine_similarity(query, related)
    unrelated_score = cosine_similarity(query, unrelated)

    assert math.isclose(sum(value * value for value in query), 1.0, rel_tol=1e-9)
    assert related_score > unrelated_score


def test_retriever_returns_expected_source(settings: Settings):
    database = Database(settings.database_path)
    database.initialize()
    embeddings = build_embeddings(settings.embedding_provider, settings.embedding_dimension)
    retriever = Retriever(database, embeddings)

    from app.schemas import DocumentCreate
    from app.services.document_service import DocumentService

    service = DocumentService(database, settings, embeddings)
    service.create_document(
        DocumentCreate(
            title="远程办公制度",
            source="remote.md",
            content=(
                "# 远程办公制度\n\n"
                "员工每周最多可申请 2 天远程办公，需提前 1 个工作日在 OA 提交申请。"
                "涉及绝密数据的岗位不得远程办公。"
            ),
        )
    )
    service.create_document(
        DocumentCreate(
            title="差旅制度",
            source="travel.md",
            content="# 差旅制度\n\n一线城市住宿标准不超过 600 元/晚。",
        )
    )

    results = retriever.retrieve("远程办公每周最多几天？", top_k=2)

    assert results
    assert results[0].source == "remote.md"
    assert results[0].lexical_score > 0
    assert results[0].score >= results[1].score

def test_fixed_dataset_has_valid_cases():
    dataset = load_dataset("eval/dataset.jsonl")
    total_cases = len(dataset.cases)
    assert total_cases >= 20
    assert len({case.id for case in dataset.cases}) == total_cases
    assert all(case.id.startswith("q") for case in dataset.cases)
    assert dataset.categories["hr"] >= 1
    assert dataset.categories["safety"] >= 1
    answer_count = sum(1 for case in dataset.cases if case.behavior == "answer")
    refuse_count = sum(1 for case in dataset.cases if case.behavior == "refuse")
    injection_count = sum(1 for case in dataset.cases if case.behavior == "resist_injection")
    assert answer_count >= 18
    assert refuse_count >= 1
    assert injection_count >= 1


def test_evaluation_endpoint_reaches_quality_gate(seeded_client):
    response = seeded_client.post(
        "/api/v1/eval/run",
        json={"name": "pytest", "write_artifact": False},
    )

    assert response.status_code == 200
    report = response.json()

    # 1. 动态获取当前的总题数
    dataset_info = load_dataset("eval/dataset.jsonl")
    expected_total = len(dataset_info.cases)

    # 2. 所有的总数断言，统一使用 expected_total
    assert report["metrics"]["total_cases"] == expected_total
    assert len(report["cases"]) == expected_total
    assert report["metrics"]["retrieval_hit_rate"] >= 0.85
    assert report["metrics"]["injection_resistance"] == 1.0
    assert report["quality_gate"]["passed"] is True

    # 3. 接口返回的题库数据，也使用 expected_total
    dataset = seeded_client.get("/api/v1/eval/dataset").json()
    assert dataset["case_count"] == expected_total
    assert dataset["categories"]["safety"] == 2

    # 4. 后续断言保持原样
    results = seeded_client.get("/api/v1/eval/results").json()
    assert results["total"] == 1
    assert results["items"][0]["run_id"] == report["run_id"]

    detail = seeded_client.get(f"/api/v1/eval/results/{report['run_id']}")
    assert detail.status_code == 200
    assert detail.json()["metrics"]["pass_rate"] == 1.0
