# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
poetry install

# Run all tests
poetry run pytest .

# Run tests for changed files only (faster iteration)
poetry run pytest . --testmon

# Run a single test file
poetry run pytest tests/test_engine.py

# Run a specific test
poetry run pytest tests/test_engine.py::test_function_name

# Watch mode
poetry run ptw

# Lint and format (via pre-commit)
poetry run pre-commit run --all-files

# Run the server manually against a local repo
poetry run seagoat-server start ~/path/to/repo

# Query the server
poetry run seagoat "search query" /path/to/repo
```

## Architecture

SeaGOAT is a local semantic code search engine with a **client-server architecture**. The server indexes a Git repository into a vector database and serves queries. The CLI client connects to the server.

### Core Data Flow

1. **Indexing**: `Repository` analyzes Git history to score files by recency/frequency (frecency). `GitFile` reads blob data from Git objects and splits content into `FileChunk`s. Each chunk is upserted into ChromaDB.
2. **Query**: The `Engine` fans out queries to two async sources simultaneously: `chroma` (vector similarity) and `ripgrep` (regex/keyword). Results are merged, scored, and returned.
3. **Scoring**: Results are ranked by a composite score: 70% vector similarity + 30% file frecency (files touched more recently/frequently rank higher). Exact regex matches also boost scores.

### Key Modules

- **`seagoat/engine.py`** — `Engine` class: the library entry point. Orchestrates indexing and querying. Can be used directly without the server.
- **`seagoat/server.py`** — Flask app + Click CLI (`seagoat-server`). Wraps `TaskQueue` to serialize all engine operations on a single worker thread. Endpoints: `POST /lines/query`, `POST /files/query`, `GET /status`.
- **`seagoat/cli.py`** — Click CLI (`gt` / `seagoat`). Sends queries to the running server over HTTP, then formats output.
- **`seagoat/repository.py`** — `Repository`: runs `git log` and `rg --files` to discover files, computes frecency scores from commit history.
- **`seagoat/gitfile.py`** — `GitFile`: reads file content from Git object store (not the working tree). `FileChunk` is a content-addressed chunk of lines with metadata.
- **`seagoat/result.py`** — `Result`, `ResultLine`, `ResultBlock`: merges hits from multiple sources, handles context lines and bridge lines (gap-filling between nearby blocks).
- **`seagoat/cache.py`** — `Cache`: pickle-based persistence keyed by a hash of (repo path + `CACHE_FORMAT_VERSION`). Bump `CACHE_FORMAT_VERSION` when adding fields that require re-analysis.
- **`seagoat/queue/`** — `BaseQueue`: single-worker thread with a `PriorityQueue`. `TaskQueue` extends it, adding `handle_*` methods for `query`, `analyze_chunk`, `get_stats`, and `maintenance`. Maintenance runs every 10s when idle, re-analyzing if the repo state hash changed.
- **`seagoat/sources/chroma.py`** — ChromaDB source: batched upsert, vector query, staleness check via `git_object_id`.
- **`seagoat/sources/ripgrep.py`** — Ripgrep source: builds an in-memory mmap cache of `path:line:content`, then runs `rg` against it for regex matching.
- **`seagoat/utils/config.py`** — YAML config merging (global → repo-level `.seagoat.yml`). Key settings: `server.port`, `server.ignorePatterns`, `server.readMaxCommits`, `server.chroma.embeddingFunction`, `server.chroma.batchSize`, `client.host`.

### Important Constraints

- **Single-threaded engine**: All `Engine` operations (indexing and querying) run on a single worker thread inside `TaskQueue`. The Flask server runs on a separate thread and enqueues tasks. Do not call engine methods from multiple threads.
- **Git-native file reading**: File contents are read from Git blob objects, not the filesystem. This means only committed content is indexed. Unstaged changes are not searched.
- **Supported file types**: Hard-coded in `seagoat/utils/file_types.py`. Only files matching the allowed extensions are indexed.
- **Cache invalidation**: The engine cache (`Cache("cache", ...)`) tracks which chunks have been analyzed via `chunks_already_analyzed`. The chroma cache invalidates stale entries using `git_object_id` on every query. If `CACHE_FORMAT_VERSION` is bumped, all caches are invalidated automatically (different hash → different folder).
- **Test isolation**: Tests use `PYTEST_CURRENT_TEST` env var (set in `conftest.py`) to switch to a separate `seagoat-pytest` cache directory. The `mock_chromadb` fixture is `autouse=True`, so all tests mock ChromaDB unless they use the `real_chromadb` fixture.

## Repository

Remote origin: `git@github.com:Hellblazer/SeaGOAT.git`

## Linting and Pre-commit

Pre-commit hooks enforce: `ruff-format`, `ruff` (linting), `pyright` (type checking), `markdownlint`, `yamlfmt`, and removal of `print` statements (except in `tests/conftest.py` and `seagoat/utils/debug.py`).
