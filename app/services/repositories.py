from __future__ import annotations

from app.models import (
    CompareTask, CompareTaskCreate, DataSource, DataSourceCreate,
    Workflow, WorkflowCreate,
)
from app.services.json_store import JsonStore
from app.utils.paths import DATASOURCES_FILE, TASKS_FILE, WORKFLOWS_FILE


datasource_store = JsonStore[DataSource, DataSourceCreate](DATASOURCES_FILE, DataSource)
task_store = JsonStore[CompareTask, CompareTaskCreate](TASKS_FILE, CompareTask)
workflow_store = JsonStore[Workflow, WorkflowCreate](WORKFLOWS_FILE, Workflow)
