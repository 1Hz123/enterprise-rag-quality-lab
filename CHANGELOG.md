# Changelog

All notable changes to this project are documented here. The format loosely follows Keep a Changelog and the project uses semantic versioning while pre-1.0.

## [0.2.0] - 2026-10-28

### Added

- Policy-version metadata: `policy_key`, `version`, `effective_from`, `effective_until` on documents.
- `as_of`-aware chat and document-list APIs. Answers retrieve only the versions effective on the requested date.
- Effective-window overlap guard: ingesting a second active version for the same `policy_key` returns HTTP 409.
- Citation and retrieval payloads now expose policy version and effective dates for audit.
- Markdown frontmatter support for policy metadata and optional title override.
- Historical `employee-handbook-2025.md` sample and a 2026 current version, demonstrating 8-day vs 10-day annual leave.
- Five policy-version tests; suite is now 32 tests.
- Per-case `as_of` in the 20-question benchmark, making baselines independent of the wall clock.

### Changed

- Restored the setuptools package configuration, `app` package discovery and working `enterprise-rag-lab` console entry point.
- Aligned project Python support with README and CI (`>=3.11`).
- Restored pytest, coverage and Ruff configuration; removed the stale `uv` package scaffold and lock file.
- Test fixtures can use `RAG_LAB_TEST_DIR` in restricted environments instead of relying on per-test temporary directories.
- Database schema migrated to v2 with additive policy columns and index.

### Fixed

- README install/test instructions now match the actual package configuration.
- Local test runs no longer scan unrelated pytest artifacts at the repository root.
- Benchmark policy dates are pinned, so CI results no longer change with the current date.

## [0.1.0] - 2026-10-26

### Added

- FastAPI application factory with SQLite persistence and lifespan-managed services.
- Document ingestion from Markdown/TXT files, JSON payloads and multipart uploads.
- Section-aware chunking with character budgets and intra-section overlap.
- Deterministic 384-dimensional signed hashing embeddings for Chinese and English text.
- Hybrid retrieval combining cosine similarity, BM25 and title coverage.
- Answerability gate based on hybrid score and corpus-size-independent query coverage.
- Extractive generator with multi-document source selection and citation markers.
- Optional OpenAI-compatible chat-completions generator.
- Direct prompt-injection guard and indirect-injection context sanitizer.
- Citation validation and persisted run traces with retrieval scores.
- Fixed 20-question evaluation dataset covering HR, security, IT, procurement, support, cross-document reasoning, refusal and prompt injection.
- Evaluator with retrieval hit rate, MRR, keyword recall, citation precision, refusal accuracy, injection resistance and latency percentiles.
- Quality gates and a committed baseline report (`eval/results/baseline.json`).
- CLI tools for corpus ingestion (`scripts/ingest.py`) and benchmarking (`eval/run_eval.py`).
- 27 API, RAG, evaluation and injection tests.
- GitHub Actions CI for Python 3.11 and 3.12 with lint, tests, coverage and benchmark gate.
- Architecture documentation, experiment log, configuration template and MIT license.

### Known limitations

- The default hashing embedding is lexical and does not handle paraphrase robustly.
- The extractive generator prioritizes groundedness over natural language quality.
- The 20-question benchmark is intentionally small and will overfit; it is a regression baseline, not a general capability claim.
- Authentication, tenant isolation, document ACLs, rate limiting and PII redaction are out of scope for this lab release.
