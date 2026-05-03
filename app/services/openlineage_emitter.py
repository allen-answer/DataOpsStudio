from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models import Artifact, AssetKind, AssetRef, Workflow, WorkflowRun


PRODUCER = "https://github.com/allen-answer/DataOpsStudio"
DEFAULT_NAMESPACE = "dataops-studio"
CUSTOM_FACET_SCHEMA = "https://dataops-studio.local/openlineage/facets/dataops-v1.json"


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
