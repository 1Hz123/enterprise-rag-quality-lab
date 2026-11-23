"""Shared fixtures for API and RAG tests."""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
import warnings
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_FALLBACK_TMP_ROOT = PROJECT_ROOT / ".test-tmp"


def pytest_sessionstart(session: pytest.Session) -> None:
    """Remove temp dirs left behind by an interrupted earlier run."""

    if os.environ.get("RAG_LAB_TEST_DIR"):
        return
    for path in PROJECT_ROOT.glob("rag-lab-tests-*"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def settings() -> Iterator[Settings]:
    """Create an isolated database without requiring a per-test directory.

    CI normally uses an OS temp directory. Restricted Windows sandboxes can
    point ``RAG_LAB_TEST_DIR`` at a pre-created writable directory. If
    ``tempfile`` itself falls back to the repository root, keep every test
    database in one ignored ``.test-tmp`` directory instead of creating a
    separate directory per test.
    """

    configured_root = os.environ.get("RAG_LAB_TEST_DIR")
    if configured_root:
        root = Path(configured_root)
        root.mkdir(parents=True, exist_ok=True)
        remove_root = False
    elif Path(tempfile.gettempdir()).resolve() == PROJECT_ROOT.resolve():
        root = _FALLBACK_TMP_ROOT
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

    for suffix in ("", "-wal", "-shm", "-journal"):
        # Some sandboxes block deletes; the next run can clean the ignored
        # .test-tmp directory from the shell.
        with suppress(OSError):
            Path(f"{database_path}{suffix}").unlink(missing_ok=True)
    if remove_root:
        try:
            shutil.rmtree(root)
        except OSError as exc:
            warnings.warn(
                f"could not remove temporary test directory {root}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def seeded_client(settings: Settings) -> Iterator[TestClient]:
    seeded_settings = settings.model_copy(update={"auto_ingest_sample_docs": True})
    with TestClient(create_app(seeded_settings)) as test_client:
        yield test_client
