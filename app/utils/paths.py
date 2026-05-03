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
WORKFLOW_TEMPLATES_FILE = CONFIG_DIR / "workflow_templates.json"
LINEAGE_AI_CONFIG_FILE = CONFIG_DIR / "lineage_ai.json"
LOCAL_SECRET_KEY_FILE = CONFIG_DIR / ".dataops_secret.key"
WORKFLOW_RUNS_DIR = RESULTS_DIR / "workflow_runs"
LINEAGE_GROUP_RULES_YAML = CONFIG_DIR / "lineage_group_rules.yml"
LINEAGE_GROUP_RULES_JSON = CONFIG_DIR / "lineage_group_rules.json"
USERS_FILE = CONFIG_DIR / "users.json"
PROJECTS_FILE = CONFIG_DIR / "projects.json"
AUDIT_LOG_FILE = LOGS_DIR / "audit.jsonl"


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
    if not WORKFLOW_TEMPLATES_FILE.exists():
        WORKFLOW_TEMPLATES_FILE.write_text("[]", encoding="utf-8")
    if not LINEAGE_AI_CONFIG_FILE.exists():
        LINEAGE_AI_CONFIG_FILE.write_text("{}", encoding="utf-8")
    if not USERS_FILE.exists():
        USERS_FILE.write_text("[]", encoding="utf-8")
    if not PROJECTS_FILE.exists():
        PROJECTS_FILE.write_text("[]", encoding="utf-8")
