from __future__ import annotations

DOCUMENT = {
    "title": "测试版请假制度",
    "source": "leave-policy.md",
    "content": (
        "# 测试版请假制度\n\n"
        "## 年假\n\n"
        "员工入职满 1 年不满 3 年，年假为 5 天；入职满 3 年不满 5 年，年假为 10 天。"
        "年假应在当年 12 月 31 日前使用，未休完的部分按公司规定处理。\n\n"
        "## 病假\n\n"
        "连续病假超过 2 个工作日的，须提供二级以上医院出具的病假证明。"
        "病假申请应在当天 10:00 前提交给直属主管。"
    ),
}


def test_create_list_get_and_delete_document(client):
    created = client.post("/api/v1/documents", json=DOCUMENT)
    assert created.status_code == 201
    document = created.json()
    assert document["title"] == DOCUMENT["title"]
    assert document["chunk_count"] >= 1
    assert document["status"] == "indexed"
    document_id = document["id"]

    listing = client.get("/api/v1/documents").json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == document_id

    detail = client.get(f"/api/v1/documents/{document_id}")
    assert detail.status_code == 200
    assert "二级以上医院" in detail.json()["content"]

    deleted = client.delete(f"/api/v1/documents/{document_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/documents/{document_id}").status_code == 404


def test_duplicate_content_is_rejected(client):
    assert client.post("/api/v1/documents", json=DOCUMENT).status_code == 201
    duplicate = client.post(
        "/api/v1/documents",
        json={**DOCUMENT, "title": "重复文档", "source": "duplicate.md"},
    )

    assert duplicate.status_code == 409
    assert "document_id" in duplicate.json()["detail"]


def test_upload_markdown_document(client):
    content = "# 上传制度\n\nVPN 申请通过后 1 个工作日内开通。\n"
    response = client.post(
        "/api/v1/documents/upload",
        files={"file": ("uploaded-policy.md", content.encode("utf-8"), "text/markdown")},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["title"] == "上传制度"
    assert payload["source"] == "uploaded-policy.md"
    assert payload["chunk_count"] == 1


def test_upload_rejects_non_text_file(client):
    response = client.post(
        "/api/v1/documents/upload",
        files={"file": ("policy.pdf", b"%PDF-1.7", "application/pdf")},
    )

    assert response.status_code == 400


def test_document_validation_rejects_blank_and_traversal(client):
    blank = client.post(
        "/api/v1/documents",
        json={"title": "blank", "content": "   "},
    )
    traversal = client.post(
        "/api/v1/documents",
        json={**DOCUMENT, "source": "../secret.md"},
    )

    assert blank.status_code == 422
    assert traversal.status_code == 422


def test_reindex_and_missing_document(client):
    document = client.post("/api/v1/documents", json=DOCUMENT).json()

    reindexed = client.post(f"/api/v1/documents/{document['id']}/reindex")
    assert reindexed.status_code == 200
    assert reindexed.json()["chunk_count"] == document["chunk_count"]

    assert client.post("/api/v1/documents/not-found/reindex").status_code == 404
    assert client.delete("/api/v1/documents/not-found").status_code == 404


def test_sample_corpus_ingestion_is_idempotent(client):
    first = client.post("/api/v1/documents/ingest-sample")
    second = client.post("/api/v1/documents/ingest-sample")

    assert first.status_code == 200
    assert first.json()["ingested"] == 7
    assert second.status_code == 200
    assert second.json()["skipped"] == 7
    assert client.get("/health").json()["documents"] == 7
