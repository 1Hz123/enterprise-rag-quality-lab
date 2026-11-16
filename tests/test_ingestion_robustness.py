from __future__ import annotations

from datetime import date

import pytest

from app.knowledge.embeddings import cosine_similarity, tokenize
from app.knowledge.loader import LoadedDocument, load_bytes, load_directory
from app.knowledge.splitter import split_document, split_text


def test_chunk_inherits_provenance_and_heading_path():
    document = LoadedDocument(
        title="员工手册",
        source="policies/hr/employee-handbook.md",
        content=(
            "# 员工手册\n\n"
            "## 3. 休假\n\n"
            "### 3.1 年假\n\n"
            "入职满三年，年假为 10 天。年假应在当年使用。"
        ),
        effective_from=date(2026, 1, 1),
        metadata={"policy_key": "employee-handbook", "version": "2026"},
    )

    chunks = split_document(document, chunk_size=200, overlap=20)

    assert chunks
    assert all(chunk.metadata["source"] == document.source for chunk in chunks)
    assert all(chunk.metadata["checksum"] == document.checksum for chunk in chunks)
    assert all(chunk.metadata["effective_from"] == "2026-01-01" for chunk in chunks)
    expected_path = "员工手册 > 3. 休假 > 3.1 年假"
    assert any(chunk.metadata["heading_path"] == expected_path for chunk in chunks)


def test_headings_inside_code_fences_are_not_sections():
    document = LoadedDocument(
        title="安装手册",
        source="install.md",
        content=(
            "# 安装手册\n\n"
            "## 安装依赖\n\n"
            "```bash\n"
            "# 这里是代码注释，不是标题\n"
            "uv sync\n"
            "```\n\n"
            "## 启动\n\n"
            "运行 uvicorn。"
        ),
    )

    chunks = split_document(document, chunk_size=300, overlap=20)

    assert chunks
    assert all(chunk.section != "这里是代码注释，不是标题" for chunk in chunks)
    heading_paths = [chunk.metadata.get("heading_path") or "" for chunk in chunks]
    assert all("这里是代码注释" not in path for path in heading_paths)
    assert document.title == "安装手册"


def test_horizontal_rule_is_not_parsed_as_frontmatter():
    raw = b"---\n# Not frontmatter\n\nBody text.\n---\n"
    loaded = load_bytes("note.md", raw)

    assert "Body text." in loaded.content
    assert loaded.metadata.get("title") is None


def test_gbk_document_is_loaded_with_encoding_metadata():
    raw = "员工年假为 10 天。".encode("gb18030")

    loaded = load_bytes("gbk-policy.txt", raw, content_type="text/plain")

    assert "员工年假" in loaded.content
    assert loaded.metadata["encoding"] == "gb18030"


def test_mixed_latin_chinese_keeps_both_tokens():
    tokens = tokenize("Python代码和Docker镜像")

    assert "代码" in tokens
    assert "镜像" in tokens
    assert "python代" not in tokens
    assert "docker镜" not in tokens


def test_embedding_dimension_mismatch_is_not_silently_zero():
    with pytest.raises(ValueError, match="dimension mismatch"):
        cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0])


def test_english_sentences_are_split_on_periods():
    text = "This is sentence one. This is sentence two. " * 12
    chunks = split_text(text, chunk_size=120, overlap=20)

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 120 for chunk in chunks)
    assert all(chunk.text.rstrip().endswith(".") for chunk in chunks)


def test_directory_batch_skips_unreadable_file(settings):
    root = settings.database_path.parent
    bad = root / "bad-corpus.md"
    bad.write_bytes(b"\x00\x00\x00")

    try:
        documents = load_directory(root)
    finally:
        bad.unlink(missing_ok=True)

    assert documents == []
