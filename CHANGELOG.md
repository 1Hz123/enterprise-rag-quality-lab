# Changelog

All notable changes to this project are documented here. The format loosely follows Keep a Changelog and the project uses semantic versioning.

## [1.1.0] - 2026-11-18

### Added

- Raw generation audit: `raw_answer`, `invalid_citation_count` and `invalid_citation_rate`; invalid markers are removed from the answer but still fail the 2% quality gate.
- Document `fingerprint` with a unique index; same content with a different effective window can coexist, while same content/window is still a duplicate.
- `document_versions` snapshots and `GET /api/v1/documents/{id}/versions`.
- Sentence-level citation `quote` with `quote_verified`; Auditor rejects quotes not present in the cited chunk.
- Structured numeric comparison (`metrics.comparison`) for multi-point policy questions.
- OpenAI generator retry plus deterministic extractive fallback (`fallback_used`).
- Experience reuse across days within an open policy window, 30-day TTL, safe stale deletion and runtime signature in `context_hash`.
- CI baseline regression gate (`baseline - 2%`), `uv sync --frozen`, Python 3.12 and 80% coverage floor.

### Changed

- Multi-year comparison uses the last day of each historical year and the request date for the current year; supports up to five points.
- Experience quality score now follows the newly written answer instead of keeping `MAX(score)`.
- Table rows are no longer unconditionally dropped from extractive generation.
- SQLite schema is version 4 with `documents.fingerprint`, `document_versions` and `experiences.signature`.

### Fixed

- Invalid citations can no longer disappear from the evaluation because the pipeline cleaned them before scoring.
- Same prose with different effective periods is no longer rejected as a duplicate.
- Multi-year comparisons no longer use an arbitrary June 1 snapshot.
- Markdown table separator lines are filtered without dropping content rows.

## [1.0.1] - 2026-11-16

### Added

- Document-aware `split_document(LoadedDocument)`; every chunk now inherits source, checksum, policy metadata, effective dates, relative path, heading path and embedding model/dimension.
- Hierarchical `heading_path` metadata for nested Markdown headings.
- GB18030 fallback decoding for Chinese enterprise text files, with encoding metadata.
- Frontmatter `effective_to` compatibility alias, date normalization to ISO and typed `effective_from/effective_until`.
- Directory scanning ignores VCS, virtualenv, node_modules and build/cache directories.
- Per-file load failure isolation (`strict=False` by default) with warnings.
- Eight ingestion/splitter regression tests; suite is now 47 tests.

### Changed

- `cosine_similarity` now raises on empty or mismatched embedding dimensions instead of silently returning 0.
- Chunk overlap must not exceed half of `chunk_size`.
- Mixed Latin/Chinese tokenization no longer swallows the first Chinese character of a following word.
- English periods are sentence boundaries only when followed by whitespace.
- Markdown headings inside fenced code blocks are ignored by both loader title inference and splitter sections.
- Leading `---` horizontal rules are no longer mistaken for frontmatter.
- `embeddings.normalize_text` renamed to `normalize_for_retrieval` to avoid collision with loader normalization.

### Fixed

- Provenance no longer disappears between `LoadedDocument`, splitter and stored chunks.
- Dates are canonical ISO and chronologically comparable, with typed fields carried through ingestion.
- A single unreadable file no longer aborts a whole directory import.
- `Python代码` now produces the `代码` bigram instead of `python代`.

## [0.3.1] - 2026-11-14

### Added

- Multi-stage-friendly `python:3.11-slim` Dockerfile with non-root user, health check and `EXPOSE 8000`.
- `.dockerignore` for a small, secret-free build context.
- `security` optional dependency group with `pip-audit`, `bandit` and `pip-licenses`.
- License inventory, runtime/full CVE reports and Bandit report under `docs/audit/`.
- Security audit document with reproduction commands and risk boundaries.
- CI jobs for dependency/license/SAST security and Docker build + health smoke test.
- README Docker and security sections.

### Changed

- All diagnostic CLI `print()` calls were replaced with logging; stdout remains clean for JSON/CI output.
- Runtime, development and security dependency groups are explicitly separated in `pyproject.toml`.
- Docker image uses a non-root user and a runtime-only install.
- `.gitignore` now covers all `*.db`, caches and audit environments.

### Fixed

- Bandit B608 dynamic SQL findings replaced with static parameterized SQL.
- Bandit B310 URL-open finding mitigated by validating `http(s)` schemes in the OpenAI-compatible generator.
- Upgraded pytest from 8.4.2 to 9.2.0 to remove PYSEC-2026-1845 / CVE-2025-71176 from the development dependency set.

## [0.3.0] - 2026-11-14

### Added

- Task-based Intelligent Organization RAG runtime with explicit Coordinator, Retriever, Reasoner and Auditor agents.
- Multi-hop temporal comparison: two-year questions are split into separate `as_of` subtasks, answered independently, then merged with globally renumbered citations.
- Experience Library backed by SQLite: audited high-quality single-hop answers are stored, context-hash checked and safely reused.
- Feedback loop at `POST /api/v1/runs/{run_id}/feedback`: ratings 4–5 promote an experience; ratings 1–2 suppress it.
- Controlled evaluation-driven evolution at `POST /api/v1/organization/evolve`: bounded coverage-threshold search, proposal by default and explicit opt-in application.
- Organization inspection APIs for agent roster, experience statistics and experience list/delete.
- Agent traces, quality score, plan strategy and experience IDs in chat responses and run metrics.
- Chinese-numeral query normalization and digit/unit token alignment without weakening coverage safety.
- `q021` Chinese-numeral benchmark case; benchmark expanded to 21 cases.
- Seven organization, experience, feedback and evolution tests; suite is now 39 tests.
- DB schema v3 with `experiences` and `feedback` tables.

### Changed

- `ChatService` now calls the Intelligent Organization runtime instead of the single pipeline directly.
- `RAGPipeline` exposes retrieval-only and answer-from-retrieval methods for agent reuse without duplicate retrieval.
- The historical Auditor responsibility now covers citation validity, effective-date windows, multi-task coverage and safety.
- Documentation, API examples and experiment log updated for the intelligent-organization design.

### Fixed

- Removed the unsafe “short questions bypass query coverage” workaround; Chinese numerals are normalized instead.
- Removed duplicated, report-mutating defensive-audit logic from the evaluation CLI; the evaluator and Auditor agent now own those checks.

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
