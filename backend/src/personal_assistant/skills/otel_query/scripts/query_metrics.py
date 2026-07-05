"""Query Prometheus metrics via Grafana proxy.

Reads JSON query parameters from stdin, calls the Prometheus API through the
Grafana datasource proxy, and prints the result as JSON to stdout.

Input format::

    {"query": "histogram_quantile(0.95, sum(rate(...)))"}

Output format: Prometheus API JSON response (``{"status": "success", "data": {...}}``)
or ``{"error": "..."}`` on failure.

Note: This queries Prometheus instant API (current time). Use PromQL functions
like ``rate(...[5m])`` or ``[5m]`` range selectors within the query string
to specify time windows.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_PROMETHEUS_PROXY_URL = ""


def query_metrics(
    *,
    promql: str,
    proxy_url: str | None = None,
) -> dict[str, Any]:
    """Query Prometheus via the Grafana datasource proxy.

    Returns the parsed JSON response from Prometheus, or a dict with an
    ``"error"`` key on failure.
    """
    base = (
        proxy_url
        or os.getenv("OTEL_PROMETHEUS_PROXY_URL")
        or DEFAULT_PROMETHEUS_PROXY_URL
    ).rstrip("/")

    params: dict[str, str] = {"query": promql}

    url = f"{base}/query?{urllib.parse.urlencode(params)}"
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
    if not payload.get("query"):
        print(json.dumps({"error": "Missing required parameter: query"}))
        return 1
    result = query_metrics(
        promql=str(payload["query"]),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
