"""SQLite persistence layer.

The lab deliberately uses SQLite so that every experiment is local,
inspectable and reproducible. Connections are short-lived and safe to use from
the synchronous FastAPI worker threads.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    source          TEXT NOT NULL,
    content_type    TEXT NOT NULL DEFAULT 'text/markdown',
    content         TEXT NOT NULL,
    checksum        TEXT NOT NULL,
    policy_key      TEXT,
    version         TEXT,
    effective_from  TEXT,
    effective_until TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'indexed',
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_checksum ON documents(checksum);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source);

CREATE TABLE IF NOT EXISTS chunks (
    id             TEXT PRIMARY KEY,
    document_id    TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal        INTEGER NOT NULL,
    section        TEXT,
    content        TEXT NOT NULL,
    token_estimate INTEGER NOT NULL DEFAULT 0,
    embedding_json TEXT NOT NULL,
    metadata_json  TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL,
    UNIQUE(document_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);

CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    question        TEXT NOT NULL,
    answer          TEXT NOT NULL,
    refused         INTEGER NOT NULL DEFAULT 0,
    security_blocked INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,
    mode            TEXT NOT NULL,
    retrieval_json  TEXT NOT NULL DEFAULT '[]',
    citations_json  TEXT NOT NULL DEFAULT '[]',
    metrics_json    TEXT NOT NULL DEFAULT '{}',
    prompt_version  TEXT NOT NULL,
    latency_ms      REAL NOT NULL DEFAULT 0,
    error           TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);

CREATE TABLE IF NOT EXISTS eval_runs (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    dataset_name  TEXT NOT NULL,
    status        TEXT NOT NULL,
    metrics_json  TEXT NOT NULL DEFAULT '{}',
    cases_json    TEXT NOT NULL DEFAULT '[]',
    config_json   TEXT NOT NULL DEFAULT '{}',
    report_json   TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    finished_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_eval_runs_created ON eval_runs(created_at DESC);
"""


def utc_now() -> str:
    """Return a sortable, timezone-aware UTC timestamp."""

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class Database:
    """Small wrapper that owns schema creation and connection hygiene."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create or migrate tables and indexes; safe to call repeatedly."""

        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA_SQL)
            self._migrate_document_policy_columns(connection)
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_documents_policy_window
                ON documents(policy_key, effective_from, effective_until)
                """
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _migrate_document_policy_columns(connection: sqlite3.Connection) -> None:
        """Add policy-version columns to databases created before v0.2."""

        existing = {
            row["name"] for row in connection.execute("PRAGMA table_info(documents)").fetchall()
        }
        additions = {
            "policy_key": "TEXT",
            "version": "TEXT",
            "effective_from": "TEXT",
            "effective_until": "TEXT",
        }
        for name, sql_type in additions.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE documents ADD COLUMN {name} {sql_type}")

    def ping(self) -> bool:
        with self.connect() as connection:
            row = connection.execute("SELECT 1 AS ok").fetchone()
        return bool(row and row["ok"] == 1)

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(sql, params).fetchone()

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute(sql, params).fetchall())

    def counts(self) -> dict[str, int]:
        with self.connect() as connection:
            documents = connection.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
            chunks = connection.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
            runs = connection.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
        return {"documents": int(documents), "chunks": int(chunks), "runs": int(runs)}
