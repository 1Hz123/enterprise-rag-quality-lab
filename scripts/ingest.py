#!/usr/bin/env python
"""Ingest the sample corpus or an explicit file/directory.

Examples:
    python scripts/ingest.py
    python scripts/ingest.py --source data/sample_docs --database data/lab.db
    python scripts/ingest.py --reindex
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import build_settings
from app.db import Database
from app.knowledge.embeddings import build_embeddings
from app.knowledge.loader import DocumentLoadError, load_file
from app.schemas import DocumentCreate
from app.services.document_service import (
    DocumentService,
    DuplicateDocumentError,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest documents into the RAG lab.")
    parser.add_argument(
        "--source",
        type=Path,
        default=PROJECT_ROOT / "data" / "sample_docs",
        help="File or directory to ingest (default: data/sample_docs).",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="SQLite database path (default: RAG_LAB_DATABASE_PATH or data/rag_lab.db).",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Rebuild chunks and embeddings for all existing documents.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    overrides: dict[str, object] = {}
    if args.database:
        overrides["database_path"] = args.database
    settings = build_settings(**overrides)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)

    database = Database(settings.database_path)
    database.initialize()
    embeddings = build_embeddings(settings.embedding_provider, settings.embedding_dimension)
    service = DocumentService(database, settings, embeddings)

    try:
        if args.reindex:
            count = service.reindex_all()
            payload = {"reindexed": count, "database": str(settings.database_path)}
        elif args.source.is_dir():
            result = service.ingest_directory(args.source)
            payload = {
                **result.model_dump(mode="json"),
                "database": str(settings.database_path),
            }
        elif args.source.is_file():
            loaded = load_file(
                args.source,
                max_bytes=settings.max_document_bytes,
            )
            detail = service.create_document(
                DocumentCreate(
                    title=loaded.title,
                    content=loaded.content,
                    source=loaded.source,
                    content_type=loaded.content_type,
                    metadata=loaded.metadata,
                )
            )
            payload = {
                "ingested": 1,
                "updated": 0,
                "skipped": 0,
                "documents": [detail.model_dump(mode="json", exclude={"content"})],
                "database": str(settings.database_path),
            }
        else:
            print(f"source not found: {args.source}", file=sys.stderr)
            return 2
    except DuplicateDocumentError as exc:
        print(f"duplicate document skipped: {exc.document_id}", file=sys.stderr)
        return 3
    except (DocumentLoadError, ValueError) as exc:
        print(f"ingestion failed: {exc}", file=sys.stderr)
        return 1

    counts = database.counts()
    payload["counts"] = counts
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"done: ingested={payload.get('ingested', 0)} "
            f"updated={payload.get('updated', 0)} skipped={payload.get('skipped', 0)} "
            f"reindexed={payload.get('reindexed', 0)} "
            f"documents={counts['documents']} chunks={counts['chunks']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
