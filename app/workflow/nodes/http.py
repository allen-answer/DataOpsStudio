"""http 节点：发 GET/POST 请求，返回 status / body / json / headers。"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse


_HTTP_RESPONSE_BYTE_CAP = 256 * 1024     # 256 KB — enough for webhook responses, blocks log dumps


def run_http_node(config: dict[str, Any], variables: dict[str, str], **_: Any) -> dict[str, Any]:
    """Issue an HTTP request and return { status, body, headers }.

    config: { url: required, method? (default GET), headers? (dict),
              body? (string), timeout_seconds? (default 30),
              expect_status? (int — fail node if response status differs) }
    """
    url = str(config.get("url") or "").strip()
    if not url:
        raise ValueError("http node requires config.url")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"http node only supports http(s) urls, got {parsed.scheme!r}")
    method = str(config.get("method") or "GET").upper()
    headers = config.get("headers") or {}
    if not isinstance(headers, dict):
        raise ValueError("http node config.headers must be an object")
    body_text = config.get("body")
    body_bytes = body_text.encode("utf-8") if isinstance(body_text, str) and body_text else None
    timeout = float(config.get("timeout_seconds") or 30)

    request = urllib.request.Request(
        url,
        data=body_bytes,
        method=method,
        headers={str(k): str(v) for k, v in headers.items()},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(_HTTP_RESPONSE_BYTE_CAP + 1)
            truncated = len(raw) > _HTTP_RESPONSE_BYTE_CAP
            body = raw[:_HTTP_RESPONSE_BYTE_CAP].decode("utf-8", errors="replace")
            status = response.status
            response_headers = dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        # Non-2xx still reaches us as HTTPError. Surface body so users can debug.
        body_bytes_err = exc.read(_HTTP_RESPONSE_BYTE_CAP) if hasattr(exc, "read") else b""
        return {
            "status": exc.code,
            "body": body_bytes_err.decode("utf-8", errors="replace"),
            "headers": dict(exc.headers.items()) if exc.headers else {},
            "error": str(exc),
        }

    expect_status = config.get("expect_status")
    if expect_status is not None and int(expect_status) != status:
        raise ValueError(f"http node expected status {expect_status}, got {status}")

    parsed_json: Any = None
    if body and body.lstrip().startswith(("{", "[")):
        try:
            parsed_json = json.loads(body)
        except (ValueError, TypeError):
            parsed_json = None

    return {
        "status": status,
        "body": body,
        "json": parsed_json,
        "headers": response_headers,
        "truncated": truncated,
    }
