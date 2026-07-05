"""Query Jaeger for traces via its REST API.

Reads JSON query parameters from stdin, calls the Jaeger API, and prints the
result as JSON to stdout.

Input format::

    {"service": "frontend", "operation": null, "lookback": "15m",
     "limit": 10, "min_duration_ms": null, "max_duration_ms": null}

Output format: Jaeger API JSON response (``{"data": [...], "total": N, ...}``)
or ``{"error": "..."}`` on failure.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_JAEGER_API_URL = ""


def query_traces(
    *,
    service: str,
    operation: str | None = None,
    lookback: str = "15m",
    limit: int = 10,
    min_duration_ms: int | None = None,
    max_duration_ms: int | None = None,
    api_url: str | None = None,
) -> dict[str, Any]:
    """Search Jaeger for traces matching the given criteria.

    Returns the parsed JSON response from the Jaeger API, or a dict with an
    ``"error"`` key on failure.
    """
    base = (api_url or os.getenv("OTEL_JAEGER_API_URL") or DEFAULT_JAEGER_API_URL).rstrip("/")
    params: dict[str, str] = {
        "service": service,
        "limit": str(max(1, limit)),
        "lookback": lookback or "15m",
    }
    if operation:
        params["operation"] = operation
    if min_duration_ms is not None:
        params["minDuration"] = f"{min_duration_ms}ms"
    if max_duration_ms is not None:
        params["maxDuration"] = f"{max_duration_ms}ms"

    url = f"{base}/traces?{urllib.parse.urlencode(params)}"
    try:
        request = urllib.request.Request(url, method="GET")
        request.add_header("Accept", "application/json")
        with urllib.request.urlopen(request, timeout=15.0) as response:
            body = response.read().decode("utf-8")
            return json.loads(body)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"error": str(exc), "url": url}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    payload = json.loads(sys.stdin.read() or "{}")
    if not payload.get("service"):
        print(json.dumps({"error": "Missing required parameter: service"}))
        return 1
    result = query_traces(
        service=str(payload["service"]),
        operation=payload.get("operation"),
        lookback=str(payload.get("lookback", "15m")),
        limit=int(payload.get("limit", 10)),
        min_duration_ms=int(payload["min_duration_ms"]) if payload.get("min_duration_ms") is not None else None,
        max_duration_ms=int(payload["max_duration_ms"]) if payload.get("max_duration_ms") is not None else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
