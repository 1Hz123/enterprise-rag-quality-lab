#!/usr/bin/env python
"""Operate the Redis cache: SCAN + UNLINK prefix cleanup.

Examples:
    python scripts/cache_cli.py clear --namespace retrieval
    python scripts/cache_cli.py clear --namespace retrieval --index-version <hash>
    python scripts/cache_cli.py clear --namespace all
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import build_settings  # noqa: E402
from app.core.cache import build_cache  # noqa: E402

logger = logging.getLogger("rag_lab.cache_cli")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clear Redis cache namespaces with SCAN.")
    parser.add_argument(
        "command",
        choices=("clear",),
        help="clear: delete keys under a namespace/index-version prefix",
    )
    parser.add_argument(
        "--namespace",
        choices=("embedding", "retrieval", "answer", "all"),
        required=True,
    )
    parser.add_argument(
        "--index-version",
        default=None,
        help="For retrieval, clear only this index version (its key namespace).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the prefix without deleting.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    settings = build_settings()
    manager = build_cache(settings)
    manager.start()
    if not manager.enabled:
        print("cache is disabled (CACHE_ENABLED=false); nothing to clear")
        manager.close()
        return 2

    base = f"{settings.cache_key_prefix}:{settings.cache_env}:"
    if args.namespace == "all":
        prefix = base
    elif args.namespace == "retrieval" and args.index_version:
        prefix = f"{base}retrieval:{args.index_version}:"
    else:
        prefix = f"{base}{args.namespace}:"

    if args.dry_run:
        print(f"would delete keys matching {prefix}*")
        manager.close()
        return 0

    deleted = manager.delete_prefix(prefix)
    print(f"deleted {deleted} key(s) matching {prefix}*")
    manager.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
