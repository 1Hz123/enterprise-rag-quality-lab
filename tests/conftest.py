"""Shared fixtures for API and RAG tests."""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings() -> Iterator[Settings]:
    """Create an isolated database without requiring a per-test directory.

    CI normally uses an OS temp directory. Restricted Windows sandboxes can
    point ``RAG_LAB_TEST_DIR`` at a pre-created writable directory.
    """

    configured_root = os.environ.get("RAG_LAB_TEST_DIR")
    if configured_root:
        root = Path(configured_root)
        root.mkdir(parents=True, exist_ok=True)
        remove_root = False
    else:
        root = Path(tempfile.mkdtemp(prefix="rag-lab-tests-"))
        remove_root = True

    run_id = uuid.uuid4().hex
    database_path = root / f"test-{run_id}.db"
    result = Settings(
        _env_file=None,
        environment="test",
        database_path=database_path,
        sample_docs_dir=PROJECT_ROOT / "data" / "sample_docs",
        eval_dataset_path=PROJECT_ROOT / "eval" / "dataset.jsonl",
        eval_results_dir=root,
        auto_ingest_sample_docs=False,
        chunk_size=520,
        chunk_overlap=80,
        top_k=5,
        min_retrieval_score=0.15,
        min_query_coverage=0.30,
    ).resolve_paths()

    yield result

    for suffix in ("", "-wal", "-shm"):
        Path(f"{database_path}{suffix}").unlink(missing_ok=True)
    if remove_root:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def seeded_client(settings: Settings) -> Iterator[TestClient]:
    seeded_settings = settings.model_copy(update={"auto_ingest_sample_docs": True})
    with TestClient(create_app(seeded_settings)) as test_client:
        yield test_client
