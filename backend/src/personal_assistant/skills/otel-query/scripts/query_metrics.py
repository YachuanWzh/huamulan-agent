"""Query Prometheus metrics via Grafana proxy — CLI tool for otel-query skill.

Usage::

    python query_metrics.py --query 'histogram_quantile(0.95, sum(rate(...)))'
    python query_metrics.py --query 'up'
"""

from __future__ import annotations

import argparse
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
    proxy_url: str | None = None,
) -> dict[str, Any]:
    """Query Prometheus via the Grafana datasource proxy."""
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
    parser = argparse.ArgumentParser(description="Query Prometheus metrics via Grafana proxy")
    parser.add_argument("--query", required=True, help="PromQL query string")

    args = parser.parse_args()
    result = query_metrics(promql=args.query)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
