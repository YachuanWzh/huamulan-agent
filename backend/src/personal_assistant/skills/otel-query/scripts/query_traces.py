"""Query Jaeger for traces via its REST API — CLI tool for otel-query skill.

Usage::

    python query_traces.py --service frontend --lookback 15m --limit 5
    python query_traces.py --service checkout --operation POST --min-duration-ms 100
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

from dotenv import find_dotenv, load_dotenv

# Load .env so the script works both as a standalone CLI and when invoked as a
# subprocess by script_tool.py (which inherits os.environ from the backend).
load_dotenv(find_dotenv(usecwd=True))

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
    """Search Jaeger for traces matching the given criteria."""
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
    parser = argparse.ArgumentParser(description="Query Jaeger for traces")
    parser.add_argument("--service", required=True, help="Service name to search")
    parser.add_argument("--operation", default=None, help="Operation name filter")
    parser.add_argument("--lookback", default="15m", help="Lookback window (default: 15m)")
    parser.add_argument("--limit", type=int, default=10, help="Max traces (default: 10)")
    parser.add_argument("--min-duration-ms", type=int, default=None, help="Min span duration (ms)")
    parser.add_argument("--max-duration-ms", type=int, default=None, help="Max span duration (ms)")

    args = parser.parse_args()
    result = query_traces(
        service=args.service,
        operation=args.operation,
        lookback=args.lookback,
        limit=args.limit,
        min_duration_ms=args.min_duration_ms,
        max_duration_ms=args.max_duration_ms,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
