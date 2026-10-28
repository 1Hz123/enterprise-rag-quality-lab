from __future__ import annotations

CHAT_DOCUMENT = {
    "title": "测试 VPN 支持流程",
    "source": "chat-vpn.md",
    "content": (
        "# 测试 VPN 支持流程\n\n"
        "## 申请与开通\n\n"
        "员工在 IT 门户提交 VPN 申请，填写访问原因和使用期限。"
        "申请经直属主管和信息安全团队审批后，IT 在 1 个工作日内完成开通。\n\n"
        "VPN 账号仅限本人使用，不得共享或转借。连续 90 天未使用的权限自动失效。"
    ),
}


def test_chat_returns_grounded_citation(client):
    assert client.post("/api/v1/documents", json=CHAT_DOCUMENT).status_code == 201

    response = client.post(
        "/api/v1/chat",
        json={"question": "VPN 申请审批通过后多久开通？", "top_k": 3},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["refused"] is False
    assert "1 个工作日" in payload["answer"]
    assert "[1]" in payload["answer"]
    assert payload["citations"]
    assert payload["citations"][0]["source"] == "chat-vpn.md"
    assert payload["retrieved_chunks"]
    assert payload["run_id"].startswith("run_")
    assert payload["prompt_version"]


def test_chat_refuses_out_of_scope_question(client):
    assert client.post("/api/v1/documents", json=CHAT_DOCUMENT).status_code == 201

    response = client.post(
        "/api/v1/chat",
        json={"question": "公司股票代码和在哪个交易所上市？"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["refused"] is True
    assert payload["citations"] == []
    assert "无法确定" in payload["answer"]


def test_chat_validates_question_and_top_k(client):
    blank = client.post("/api/v1/chat", json={"question": "   "})
    too_many = client.post(
        "/api/v1/chat",
        json={"question": "VPN 如何申请？", "top_k": 50},
    )

    assert blank.status_code == 422
    assert too_many.status_code == 422


def test_chat_can_filter_to_document_ids(client):
    first = client.post("/api/v1/documents", json=CHAT_DOCUMENT).json()
    second = client.post(
        "/api/v1/documents",
        json={
            "title": "测试差旅制度",
            "source": "chat-travel.md",
            "content": "# 测试差旅制度\n\n一线城市住宿标准不超过 600 元/晚。",
        },
    ).json()

    response = client.post(
        "/api/v1/chat",
        json={
            "question": "VPN 审批后多久开通？",
            "document_ids": [first["id"]],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["retrieved_chunks"]
    assert all(chunk["document_id"] == first["id"] for chunk in payload["retrieved_chunks"])
    assert second["id"] not in {chunk["document_id"] for chunk in payload["retrieved_chunks"]}
