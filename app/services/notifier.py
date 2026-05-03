from __future__ import annotations

import json
import logging
import os
import smtplib
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from app.models import Workflow, WorkflowRun


logger = logging.getLogger(__name__)
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("DATAOPS_NOTIFY_TIMEOUT_SECONDS", "5"))


@dataclass
class NotifyResult:
    type: str
    target: str
    ok: bool
    error: str = ""


def notify_workflow_run(
    workflow: Workflow,
    run: WorkflowRun,
    *,
    trigger: str = "manual",
    job_id: str = "",
) -> list[dict[str, Any]]:
    """Send workflow-run notifications.

    No configured targets means a quiet no-op. Individual target failures are
    logged and returned, but never raised to the workflow execution path.
    """
    event = run.status.value
    payload = _workflow_run_payload(workflow, run, trigger=trigger, job_id=job_id)
    results: list[NotifyResult] = []
    for target in _targets_for(workflow):
        if not _target_accepts_event(target, event):
            continue
        kind = str(target.get("type") or "").lower()
        try:
            if kind == "webhook":
                results.append(_send_webhook(target, payload))
            elif kind in {"wecom", "wechat_work"}:
                results.append(_send_wecom(target, payload))
            elif kind == "email":
                results.append(_send_email(target, payload))
            else:
                results.append(NotifyResult(type=kind or "unknown", target="", ok=False, error="unsupported target type"))
        except Exception as exc:
            logger.exception("workflow notification failed workflow_id=%s type=%s", workflow.id, kind)
            results.append(NotifyResult(type=kind or "unknown", target=_target_label(target), ok=False, error=str(exc)))
    return [result.__dict__ for result in results]


def _targets_for(workflow: Workflow) -> list[dict[str, Any]]:
    targets = [
        dict(item)
        for item in getattr(workflow, "notifications", []) or []
        if isinstance(item, dict) and item.get("enabled", True)
    ]
    webhook_url = os.getenv("DATAOPS_NOTIFY_WEBHOOK_URL", "").strip()
    if webhook_url:
        targets.append({"type": "webhook", "url": webhook_url, "events": ["failed"]})
    wecom_url = os.getenv("DATAOPS_NOTIFY_WECOM_WEBHOOK", "").strip()
    if wecom_url:
        targets.append({"type": "wecom", "url": wecom_url, "events": ["failed"]})
    email_to = os.getenv("DATAOPS_NOTIFY_EMAIL_TO", "").strip()
    if email_to:
        targets.append({"type": "email", "to": email_to, "events": ["failed"]})
    return targets


def _target_accepts_event(target: dict[str, Any], event: str) -> bool:
    events = target.get("events") or ["failed"]
    if isinstance(events, str):
        events = [events]
    normalized = {str(item).lower() for item in events}
    return "all" in normalized or event.lower() in normalized


def _send_webhook(target: dict[str, Any], payload: dict[str, Any]) -> NotifyResult:
    url = str(target.get("url") or "").strip()
    if not url:
        return NotifyResult(type="webhook", target="", ok=False, error="missing url")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method=str(target.get("method") or "POST").upper(),
    )
    with urllib.request.urlopen(request, timeout=_timeout(target)) as response:  # noqa: S310 - user-configured internal webhook
        status = getattr(response, "status", 200)
        if status >= 400:
            return NotifyResult(type="webhook", target=url, ok=False, error=f"http {status}")
    return NotifyResult(type="webhook", target=url, ok=True)


def _send_wecom(target: dict[str, Any], payload: dict[str, Any]) -> NotifyResult:
    url = str(target.get("url") or "").strip()
    if not url:
        return NotifyResult(type="wecom", target="", ok=False, error="missing url")
    content = (
        f"**DataOps workflow {payload['status']}**\n"
        f"> Workflow: {payload['workflow_name']} ({payload['workflow_id']})\n"
        f"> Trigger: {payload['trigger']}\n"
        f"> Started: {payload['started_at']}\n"
        f"> Error: {payload['error'] or '-'}"
    )
    body = json.dumps({"msgtype": "markdown", "markdown": {"content": content}}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=_timeout(target)) as response:  # noqa: S310 - user-configured internal webhook
        status = getattr(response, "status", 200)
        if status >= 400:
            return NotifyResult(type="wecom", target=url, ok=False, error=f"http {status}")
    return NotifyResult(type="wecom", target=url, ok=True)


def _send_email(target: dict[str, Any], payload: dict[str, Any]) -> NotifyResult:
    host = str(target.get("smtp_host") or os.getenv("DATAOPS_SMTP_HOST", "")).strip()
    if not host:
        return NotifyResult(type="email", target="", ok=False, error="missing smtp_host")
    port = int(target.get("smtp_port") or os.getenv("DATAOPS_SMTP_PORT", "25"))
    sender = str(target.get("from") or os.getenv("DATAOPS_SMTP_FROM", "dataops@localhost"))
    recipients = _email_recipients(target.get("to") or os.getenv("DATAOPS_NOTIFY_EMAIL_TO", ""))
    if not recipients:
        return NotifyResult(type="email", target="", ok=False, error="missing recipients")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = f"[DataOps] workflow {payload['status']}: {payload['workflow_name']}"
    msg.set_content(json.dumps(payload, ensure_ascii=False, indent=2))

    username = str(target.get("username") or os.getenv("DATAOPS_SMTP_USER", ""))
    password = str(target.get("password") or os.getenv("DATAOPS_SMTP_PASSWORD", ""))
    use_tls = str(target.get("tls", os.getenv("DATAOPS_SMTP_TLS", "false"))).lower() in {"1", "true", "yes"}
    with smtplib.SMTP(host, port, timeout=_timeout(target)) as smtp:
        if use_tls:
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(msg)
    return NotifyResult(type="email", target=",".join(recipients), ok=True)


def _workflow_run_payload(workflow: Workflow, run: WorkflowRun, *, trigger: str, job_id: str) -> dict[str, Any]:
    node_counts: dict[str, int] = {}
    for node in run.nodes:
        status = str(getattr(node.status, "value", node.status))
        node_counts[status] = node_counts.get(status, 0) + 1
    return {
        "event": "workflow_run",
        "trigger": trigger,
        "job_id": job_id,
        "workflow_id": workflow.id,
        "workflow_name": workflow.name,
        "project": workflow.project,
        "owner": workflow.owner,
        "status": run.status.value,
        "run_id": run.run_id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "elapsed_seconds": run.elapsed_seconds,
        "error": run.error,
        "node_status_counts": node_counts,
    }


def _email_recipients(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").replace(";", ",").split(",") if item.strip()]


def _timeout(target: dict[str, Any]) -> float:
    return float(target.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)


def _target_label(target: dict[str, Any]) -> str:
    return str(target.get("url") or target.get("to") or target.get("smtp_host") or "")
