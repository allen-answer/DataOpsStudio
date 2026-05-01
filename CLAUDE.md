# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Commands

### Backend (Python / FastAPI)

```bash
# Run locally (no Docker)
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8010 --reload

# Run tests
pytest

# Run a single test file
pytest tests/test_compare_engine.py

# Run a single test
pytest tests/test_compare_engine.py::test_identical_rows_go_to_same
```

### Frontend (Vue 3 / Vite)

```bash
cd frontend/frontend

# Dev server (proxies /api/* to http://app:8000, needs backend running)
npm run dev

# Production build — outputs to ../../static/spa/
npm run build
```

### Docker (primary dev workflow)

```bash
# Start everything (MySQL 8 + app), rebuild if code changed
wsl -d Ubuntu-20.04 -- bash -c "cd /mnt/g/work/DataOpsStudio && docker compose up -d --build"

# Restart app only (after frontend build or Python changes)
wsl -d Ubuntu-20.04 -- bash -c "cd /mnt/g/work/DataOpsStudio && docker compose restart app"

# Logs
wsl -d Ubuntu-20.04 -- docker logs dataops-studio -f
```

App runs at **http://localhost:8010**. MySQL 8 is exposed on **localhost:3307** (container name `mysql8`, internal port 3306).

After building the frontend, only a container restart is needed (static files are volume-mounted, not baked into the image).

## Architecture

### Backend

`main.py` bootstraps FastAPI, mounts `/static`, and includes the single router from `app/api/routes.py`. All HTTP endpoints live in that one file.

**Data flow for a compare run:**
1. `routes.py` → `runner.run_task(task_id)` (sync) or `jobs.submit_task_run(task_id)` (async background thread)
2. `runner` fetches rows via `dbclients/factory.py` → validates SQL via `utils/sql_guard.py` → calls `compare/engine.py`
3. `engine.compare_rows` buckets rows into `only_source / only_target / diff / same` keyed by `key_columns`
4. Results are written to `results/` as both JSON and Excel, then persisted to history

**Persistence** — no database for app state. Everything uses flat JSON files:
- `config/datasources.json` — data source configs
- `config/tasks.json` — compare task configs
- `config/jobs.json` — async job state (survives restart; in-flight jobs become `failed`)
- `results/` — per-run JSON + Excel outputs

`JsonStore` (`services/json_store.py`) is a thread-safe generic wrapper around these JSON files with mtime-based cache invalidation. Both `datasource_store` and `task_store` are module-level singletons in `services/repositories.py`.

**Async job execution** — `services/jobs.py` uses a `ThreadPoolExecutor(max_workers=2)`. Jobs support cancellation via a `cancel_requested` flag checked at each stage of `runner.run_task`.

**SQL safety** — all SQL submitted by the user passes through `utils/sql_guard.py` before execution. Only `SELECT`/`WITH` is allowed; forbidden DML/DDL keywords cause a hard rejection.

**DB drivers** — `dbclients/drivers.py` declares which Python modules map to each `DatabaseType`. `dbclients/factory.py` dynamically imports the first available driver at connect time. Currently active drivers in `requirements.txt`: `pymysql` + `cryptography` (MySQL 8 `caching_sha2_password`). Oracle, DM, DB2 drivers are optional.

### Frontend

Single-page Vue 3 app in `frontend/frontend/src/App.vue` (one large component — all state and logic lives here). Built to `static/spa/` which FastAPI serves under `/static/spa/`.

Key libraries: `@antv/g6` for lineage graphs, `@codemirror/*` for the SQL editor, `@vueuse/core` for utilities (e.g. `useClipboard`), Tailwind CSS v3 for styling.

The Vite dev server (`npm run dev`) proxies all API calls to `http://app:8000`, so it expects the backend running inside Docker. For local-only backend use, change the proxy target to `http://localhost:8010`.

### Lineage Analysis

`app/lineage/analyzer.py` — single-script SQL lineage (uses `sqlglot`).
`app/lineage/batch_analyzer.py` — multi-file ETL lineage, accepts `.sql`/`.txt`/`.zip`.

Both accept optional schema metadata files to resolve `SELECT *` and unqualified column references.

## Key Design Decisions

- **Stream compare mode** (`limits.stream_compare = true`): skips loading all rows into memory; instead streams both sides through sorted iterators and merges them. Requires both SQLs to be pre-sorted by key columns.
- **Single SQL mode** vs **Double SQL mode**: in single mode, `source_sql` runs against both source and target datasources. In double mode, `source_sql` and `target_sql` run independently.
- **`column_mappings`** in `CompareRules` lets you align columns with different names across source and target before comparison.
- **Test data** in `init_db/01_init.sql` is deliberately seeded with differences between `users`/`users_archive` and `orders`/`orders_v2` to demonstrate compare results.
