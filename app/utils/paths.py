from __future__ import annotations

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = BASE_DIR / "config"
RESULTS_DIR = BASE_DIR / "results"
LOGS_DIR = BASE_DIR / "logs"
DATASOURCES_FILE = CONFIG_DIR / "datasources.json"
TASKS_FILE = CONFIG_DIR / "tasks.json"
JOBS_FILE = CONFIG_DIR / "jobs.json"
WORKFLOWS_FILE = CONFIG_DIR / "workflows.json"
WORKFLOW_RUNS_DIR = RESULTS_DIR / "workflow_runs"


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    if not DATASOURCES_FILE.exists():
        DATASOURCES_FILE.write_text("[]", encoding="utf-8")
    if not TASKS_FILE.exists():
        TASKS_FILE.write_text("[]", encoding="utf-8")
    if not WORKFLOWS_FILE.exists():
        WORKFLOWS_FILE.write_text("[]", encoding="utf-8")
