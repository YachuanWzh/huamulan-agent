"""Query Prometheus metrics via Grafana proxy.

Reads JSON query parameters from stdin, calls the Prometheus API through the
Grafana datasource proxy, and prints the result as JSON to stdout.

Input format::

    {"query": "histogram_quantile(0.95, ...)", "time_range": "15m"}

Output format: Prometheus API JSON response (``{"status": "success", "data": {...}}``)
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

DEFAULT_PROMETHEUS_PROXY_URL = (
    "http://192.168.5.7:32807/api/datasources/proxy/uid/webstore-metrics/api/v1"
)


def query_metrics(
    *,
    promql: str,
    time_range: str | None = None,
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
    if time_range:
        # Prometheus doesn't have a time_range param directly; use `time` if provided
        # or `start`/`end` for range queries. For simplicity, we just pass the query
        # and let Prometheus handle the default (instant vector at current time).
        pass

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
        time_range=payload.get("time_range"),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
