from __future__ import annotations

import json
import logging
import os
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.models import Artifact, AssetKind, AssetRef, Workflow, WorkflowRun


logger = logging.getLogger(__name__)

PRODUCER = "https://github.com/allen-answer/DataOpsStudio"
DEFAULT_NAMESPACE = "dataops-studio"
CUSTOM_FACET_SCHEMA = "https://dataops-studio.local/openlineage/facets/dataops-v1.json"
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("DATAOPS_OPENLINEAGE_TIMEOUT_SECONDS", "5"))


@dataclass
class OpenLineageEmitResult:
    type: str
    target: str
    event_type: str
    ok: bool
    error: str = ""


def build_workflow_run_events(
    run: WorkflowRun | dict[str, Any],
    workflow: Workflow | dict[str, Any] | None = None,
    *,
    namespace: str = DEFAULT_NAMESPACE,
) -> list[dict[str, Any]]:
    """Build OpenLineage-compatible events for a workflow run.

    The function is pure: it does not push to an external collector. API
    callers, future webhook emitters, or schedulers can consume the returned
    JSON and decide where to send it.
    """
    run_model = _coerce_run(run)
    workflow_model = _coerce_workflow(workflow)

    event_types = ["START"]
    if run_model.status.value == "success":
        event_types.append("COMPLETE")
    elif run_model.status.value == "failed":
        event_types.append("FAIL")

    return [
        _event_for_run(run_model, workflow_model, event_type, namespace)
        for event_type in event_types
    ]


def emit_workflow_run_openlineage(
    workflow: Workflow,
    run: WorkflowRun,
    *,
    trigger: str = "",
    job_id: str = "",
) -> list[dict[str, Any]]:
    """POST OpenLineage events for a workflow run.

    The emitter is intentionally best-effort: external collector failures are
    returned to the caller and logged, but never raised into the workflow
    execution path. `trigger` and `job_id` are accepted so callers can keep a
    stable integration contract even though the current OpenLineage event body
    remains the pure output of `build_workflow_run_events`.
    """
    del trigger, job_id
    results: list[OpenLineageEmitResult] = []
    for target in _targets_for(workflow):
        namespace = str(target.get("namespace") or os.getenv("DATAOPS_OPENLINEAGE_NAMESPACE") or DEFAULT_NAMESPACE)
        events = build_workflow_run_events(run, workflow, namespace=namespace)
        for event in events:
            event_type = str(event.get("eventType") or "")
            if not _target_accepts_event(target, event_type):
                continue
            try:
                results.append(_send_event(target, event))
            except Exception as exc:
                logger.exception(
                    "openlineage webhook emit failed workflow_id=%s run_id=%s event_type=%s",
                    workflow.id,
                    run.run_id,
                    event_type,
                )
                results.append(
                    OpenLineageEmitResult(
                        type=_target_type(target),
                        target=_target_label(target),
                        event_type=event_type,
                        ok=False,
                        error=str(exc),
                    )
                )
    return [result.__dict__ for result in results]


def _event_for_run(
    run: WorkflowRun,
    workflow: Workflow | None,
    event_type: str,
    namespace: str,
) -> dict[str, Any]:
    terminal = event_type in {"COMPLETE", "FAIL"}
    return {
        "eventType": event_type,
        "eventTime": _event_time(run, terminal=terminal),
        "producer": PRODUCER,
        "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json",
        "run": {
            "runId": run.run_id,
            "facets": _run_facets(run),
        },
        "job": {
            "namespace": namespace,
            "name": _job_name(run, workflow),
            "facets": _job_facets(workflow),
        },
        "inputs": _input_datasets(workflow),
        "outputs": _output_datasets(run, workflow) if terminal else _output_datasets_from_workflow(workflow),
    }


def _run_facets(run: WorkflowRun) -> dict[str, Any]:
    node_status_counts: dict[str, int] = {}
    nodes: list[dict[str, Any]] = []
    for node in run.nodes:
        node_status_counts[node.status.value] = node_status_counts.get(node.status.value, 0) + 1
        nodes.append(
            {
                "nodeId": node.node_id,
                "type": node.type.value,
                "name": node.name,
                "status": node.status.value,
                "elapsedSeconds": node.elapsed_seconds,
                "reused": node.reused,
                "error": node.error,
            }
        )

    return {
        "dataops_workflow_run": _facet(
            {
                "workflowId": run.workflow_id,
                "workflowName": run.workflow_name,
                "status": run.status.value,
                "startedAt": run.started_at,
                "finishedAt": run.finished_at,
                "elapsedSeconds": run.elapsed_seconds,
                "error": run.error,
                "variableKeys": sorted(run.variables.keys()),
                "nodeStatusCounts": node_status_counts,
                "nodes": nodes,
                "resumedFrom": run.resumed_from,
            }
        )
    }


def _job_facets(workflow: Workflow | None) -> dict[str, Any]:
    if workflow is None:
        return {}
    return {
        "dataops_workflow": _facet(
            {
                "workflowId": workflow.id,
                "name": workflow.name,
                "description": workflow.description,
                "owner": workflow.owner,
                "tags": workflow.tags,
                "scheduleCron": workflow.schedule_cron,
                "project": workflow.project,
                "status": workflow.status.value,
                "nodeCount": len(workflow.nodes),
            }
        )
    }


def _input_datasets(workflow: Workflow | None) -> list[dict[str, Any]]:
    if workflow is None:
        return []
    return [_dataset_from_asset(asset, role="input") for asset in workflow.input_assets]


def _output_datasets(run: WorkflowRun, workflow: Workflow | None) -> list[dict[str, Any]]:
    outputs = _output_datasets_from_workflow(workflow)
    seen = {(item["namespace"], item["name"]) for item in outputs}
    for artifact in run.artifacts:
        item = _dataset_from_artifact(artifact)
        key = (item["namespace"], item["name"])
        if key in seen:
            continue
        seen.add(key)
        outputs.append(item)
    return outputs


def _output_datasets_from_workflow(workflow: Workflow | None) -> list[dict[str, Any]]:
    if workflow is None:
        return []
    return [_dataset_from_asset(asset, role="output") for asset in workflow.output_assets]


def _dataset_from_asset(asset: AssetRef, role: str) -> dict[str, Any]:
    return {
        "namespace": _asset_namespace(asset.kind),
        "name": asset.key,
        "facets": {
            "dataops_asset": _facet(
                {
                    "kind": asset.kind.value,
                    "role": role,
                    "description": asset.description,
                }
            )
        },
    }


def _dataset_from_artifact(artifact: Artifact) -> dict[str, Any]:
    return {
        "namespace": "dataops://artifact",
        "name": artifact.relative_path,
        "facets": {
            "dataops_artifact": _facet(
                {
                    "artifactId": artifact.id,
                    "runId": artifact.run_id,
                    "nodeId": artifact.node_id,
                    "type": artifact.type.value,
                    "fileName": artifact.name,
                    "sizeBytes": artifact.size_bytes,
                    "createdAt": artifact.created_at,
                    "description": artifact.description,
                }
            )
        },
    }


def _asset_namespace(kind: AssetKind) -> str:
    return {
        AssetKind.TABLE: "dataops://table",
        AssetKind.FILE: "dataops://file",
        AssetKind.STREAM: "dataops://stream",
    }.get(kind, "dataops://asset")


def _event_time(run: WorkflowRun, *, terminal: bool) -> str:
    value = run.finished_at if terminal and run.finished_at else run.started_at
    if value:
        return _rfc3339(value)
    return _rfc3339(datetime.now().isoformat(timespec="seconds"))


def _rfc3339(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return datetime.now().isoformat(timespec="seconds") + "Z"
    if cleaned.endswith("Z") or "+" in cleaned[10:] or "-" in cleaned[10:]:
        return cleaned
    return cleaned + "Z"


def _job_name(run: WorkflowRun, workflow: Workflow | None) -> str:
    if workflow is not None and workflow.name:
        return workflow.name
    return run.workflow_name or run.workflow_id or "workflow"


def _facet(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "_producer": PRODUCER,
        "_schemaURL": CUSTOM_FACET_SCHEMA,
        **payload,
    }


def _coerce_run(run: WorkflowRun | dict[str, Any]) -> WorkflowRun:
    if isinstance(run, WorkflowRun):
        return run
    return WorkflowRun.model_validate(run)


def _coerce_workflow(workflow: Workflow | dict[str, Any] | None) -> Workflow | None:
    if workflow is None or isinstance(workflow, Workflow):
        return workflow
    return Workflow.model_validate(workflow)


_OPENLINEAGE_TARGET_TYPES = {"openlineage", "openlineage_webhook", "marquez", "datahub"}


def _targets_for(workflow: Workflow) -> list[dict[str, Any]]:
    targets = [
        dict(item)
        for item in getattr(workflow, "notifications", []) or []
        if isinstance(item, dict)
        and item.get("enabled", True)
        and _target_type(item) in _OPENLINEAGE_TARGET_TYPES
    ]
    webhook_url = os.getenv("DATAOPS_OPENLINEAGE_WEBHOOK_URL", "").strip()
    if webhook_url:
        targets.append({
            "type": "openlineage",
            "url": webhook_url,
            "events": ["all"],
            "namespace": os.getenv("DATAOPS_OPENLINEAGE_NAMESPACE", DEFAULT_NAMESPACE),
            "timeout_seconds": os.getenv("DATAOPS_OPENLINEAGE_TIMEOUT_SECONDS", ""),
        })
    # Marquez / DataHub 通过 env 自动加 target，URL 已带固定路径或自动补全
    marquez_url = os.getenv("DATAOPS_MARQUEZ_URL", "").strip()
    if marquez_url:
        targets.append({
            "type": "marquez",
            "url": marquez_url,
            "events": ["all"],
            "namespace": os.getenv("DATAOPS_OPENLINEAGE_NAMESPACE", DEFAULT_NAMESPACE),
        })
    datahub_url = os.getenv("DATAOPS_DATAHUB_URL", "").strip()
    if datahub_url:
        targets.append({
            "type": "datahub",
            "url": datahub_url,
            "token": os.getenv("DATAOPS_DATAHUB_TOKEN", ""),
            "events": ["all"],
            "namespace": os.getenv("DATAOPS_OPENLINEAGE_NAMESPACE", DEFAULT_NAMESPACE),
        })
    return targets


# 各 collector 的标准 OpenLineage 端点路径。如果用户给的 URL 已经命中这个 suffix，
# 不再追加；否则用 base URL + suffix。
_DEFAULT_PATHS = {
    "marquez": "/api/v1/lineage",
    "datahub": "/openapi/v1/relationships/lineage",
}


def _resolve_url(target: dict[str, Any]) -> str:
    """根据 target.type 把 base URL 补全成各 collector 的 OpenLineage 端点。"""
    raw = str(target.get("url") or "").strip()
    if not raw:
        return ""
    target_type = _target_type(target)
    suffix = _DEFAULT_PATHS.get(target_type)
    if not suffix:
        return raw  # 通用 webhook：用户给什么 URL 就发到哪
    if suffix in raw or raw.endswith(suffix):
        return raw
    return raw.rstrip("/") + suffix


def _target_headers(target: dict[str, Any]) -> dict[str, str]:
    """构造请求头：base + Bearer token + 用户自定义 headers。"""
    headers = {"Content-Type": "application/json"}
    token = str(target.get("token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    custom = target.get("headers") or {}
    if isinstance(custom, dict):
        for k, v in custom.items():
            if isinstance(k, str) and isinstance(v, (str, int, float)):
                headers[k] = str(v)
    return headers


def _target_accepts_event(target: dict[str, Any], event_type: str) -> bool:
    events = target.get("events") or ["all"]
    if isinstance(events, str):
        events = [events]
    normalized = {str(item).upper() for item in events}
    return "ALL" in normalized or event_type.upper() in normalized


def _send_event(target: dict[str, Any], event: dict[str, Any]) -> OpenLineageEmitResult:
    url = _resolve_url(target)
    event_type = str(event.get("eventType") or "")
    if not url:
        return OpenLineageEmitResult(type=_target_type(target), target="", event_type=event_type, ok=False, error="missing url")
    body = json.dumps(event, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers=_target_headers(target),
        method=str(target.get("method") or "POST").upper(),
    )
    with urllib.request.urlopen(request, timeout=_timeout(target)) as response:  # noqa: S310 - user-configured internal webhook
        status = getattr(response, "status", 200)
        if status >= 400:
            return OpenLineageEmitResult(
                type=_target_type(target),
                target=url,
                event_type=event_type,
                ok=False,
                error=f"http {status}",
            )
    return OpenLineageEmitResult(type=_target_type(target), target=url, event_type=event_type, ok=True)


def _timeout(target: dict[str, Any]) -> float:
    value = target.get("timeout_seconds")
    if value in (None, ""):
        return DEFAULT_TIMEOUT_SECONDS
    return float(value)


def _target_type(target: dict[str, Any]) -> str:
    return str(target.get("type") or "openlineage").lower()


def _target_label(target: dict[str, Any]) -> str:
    return str(target.get("url") or "")
