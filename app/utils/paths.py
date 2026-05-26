from __future__ import annotations

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = BASE_DIR / "config"
RESULTS_DIR = BASE_DIR / "results"
LOGS_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"  # SQLite 落地（audit / jobs，Phase 9 ADR 6）
SQLITE_DB_FILE = DATA_DIR / "dataops.db"
DATASOURCES_FILE = CONFIG_DIR / "datasources.json"
TASKS_FILE = CONFIG_DIR / "tasks.json"
JOBS_FILE = CONFIG_DIR / "jobs.json"
WORKFLOWS_FILE = CONFIG_DIR / "workflows.json"
WORKFLOW_TEMPLATES_FILE = CONFIG_DIR / "workflow_templates.json"
LINEAGE_AI_CONFIG_FILE = CONFIG_DIR / "lineage_ai.json"
LOCAL_SECRET_KEY_FILE = CONFIG_DIR / ".dataops_secret.key"
WORKFLOW_RUNS_DIR = RESULTS_DIR / "workflow_runs"
# SQL 工作台 v0.5 导出目录
SQL_EXPORTS_DIR = RESULTS_DIR / "sql_exports"
LINEAGE_GROUP_RULES_YAML = CONFIG_DIR / "lineage_group_rules.yml"
LINEAGE_GROUP_RULES_JSON = CONFIG_DIR / "lineage_group_rules.json"
SCENARIOS_DIR = CONFIG_DIR / "scenarios"
USERS_FILE = CONFIG_DIR / "users.json"
PROJECTS_FILE = CONFIG_DIR / "projects.json"
AUDIT_LOG_FILE = LOGS_DIR / "audit.jsonl"
# SQL 工作台 v0.4 模板库
SQL_TEMPLATES_FILE = CONFIG_DIR / "sql_templates.json"
SQL_TEMPLATES_EXAMPLE_FILE = CONFIG_DIR / "sql_templates.example.json"


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    SQL_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
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
    if not SQL_TEMPLATES_FILE.exists():
        SQL_TEMPLATES_FILE.write_text("[]", encoding="utf-8")
