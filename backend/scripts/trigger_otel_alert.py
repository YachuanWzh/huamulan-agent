#!/usr/bin/env python3
"""Trigger OTEL alerts via the AlertManager webhook endpoint.

Usage:
  python trigger_otel_alert.py P0 --service frontend --alert ServiceDown --summary "frontend is down"
  python trigger_otel_alert.py P1 --service checkout --alert HighLatencyP95 --summary "P95 > 800ms"
  python trigger_otel_alert.py P2 --service recommendation --alert RpsSurge --summary "RPS +300% in 5min"
  python trigger_otel_alert.py P3 --service accounting --alert SloBurnRate --summary "error budget < 50%"

  # Batch: trigger multiple alerts at once from a JSON file
  python trigger_otel_alert.py batch alerts_batch.json

  # List available presets
  python trigger_otel_alert.py presets
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_BASE_URL = "http://192.168.5.7:8000"
WEBHOOK_PATH = "/api/otel/alerts"

# ── Severity to level mapping ──────────────────────────────────────────
SEVERITY_MAP: dict[str, str] = {
    "P0": "critical",
    "P1": "warning",
    "P2": "info",
    "P3": "none",
}

# ── Preset alerts for each level ───────────────────────────────────────
PRESETS: dict[str, dict[str, dict[str, str]]] = {
    "P0": {
        "ServiceDown": {
            "service_name": "frontend",
            "summary": "frontend service is DOWN — up == 0 for 1 minute",
            "description": "Health check failing on all instances. Users see white screen.",
        },
        "High5xxRate": {
            "service_name": "checkout",
            "summary": "checkout 5xx error rate > 50% — service largely unusable",
            "description": "50.2% of requests returning 500. Payment pipeline broken.",
        },
        "DbConnectionPoolExhausted": {
            "service_name": "cart",
            "summary": "cart DB connection pool > 95% exhausted",
            "description": "Only 3/64 connections available. Cart operations failing.",
        },
        "P95Latency5xSLO": {
            "service_name": "payment",
            "summary": "payment P95 latency 1500ms > SLO×5 (300ms × 5)",
            "description": "P95 ballooned from 280ms to 1500ms in 2 minutes.",
        },
    },
    "P1": {
        "HighLatencyP95": {
            "service_name": "checkout",
            "summary": "checkout P95 latency 800ms > SLO×2 (300ms × 2)",
            "description": "P95 increased from 290ms to 800ms over last 5 minutes.",
        },
        "High5xxRate": {
            "service_name": "shipping",
            "summary": "shipping 5xx error rate > 10% — service degraded",
            "description": "12.3% 5xx in last 5min window. Partial shipping failures.",
        },
        "High4xx5xxRate": {
            "service_name": "recommendation",
            "summary": "recommendation 4xx+5xx > 5% — elevated error rate",
            "description": "Combined 4xx+5xx at 7.8%. Recommendation cache may be stale.",
        },
        "CircuitBreakerOpen": {
            "service_name": "currency",
            "summary": "currency service circuit breaker OPEN",
            "description": "3 consecutive timeout failures triggered circuit breaker.",
        },
        "MemoryHigh": {
            "service_name": "ad",
            "summary": "ad service memory > 95% — risk of OOM",
            "description": "Memory usage at 96.2%, GC frequency increasing.",
        },
    },
    "P2": {
        "LatencyTrendRising": {
            "service_name": "recommendation",
            "summary": "recommendation P95 latency derivative > 0.5ms/min for 3 windows",
            "description": "Latency trending up: 120→145→175ms over 15 minutes.",
        },
        "SlowQueryIncrease": {
            "service_name": "product-catalog",
            "summary": "product-catalog DB slow queries increasing",
            "description": "Slow query count up 40% vs 1h ago. No user impact yet.",
        },
        "RpsSurge": {
            "service_name": "frontend-proxy",
            "summary": "frontend-proxy RPS surge +300% — potential early traffic spike",
            "description": "RPS jumped from 500 to 2000 in 5 minutes. No errors yet.",
        },
        "MemoryLeakEarlySignal": {
            "service_name": "email",
            "summary": "email service memory trend rising — possible slow leak",
            "description": "Memory growing +2MB/min over 30min, no OOM risk yet.",
        },
    },
    "P3": {
        "ErrorBudgetBurnRate": {
            "service_name": "ad",
            "summary": "ad service error budget remaining < 50%",
            "description": "Monthly error budget: 43 minutes remaining out of 86.",
        },
        "SloComplianceDrift": {
            "service_name": "quote",
            "summary": "quote service P95 drifting toward SLO threshold",
            "description": "P95: 950ms (SLO: 1000ms). 7-day trend shows approach.",
        },
        "DiskUsageWarning": {
            "service_name": "accounting",
            "summary": "accounting service disk > 80%",
            "description": "Disk usage at 82%. No immediate risk, plan expansion.",
        },
        "SpanAttributeCompleteness": {
            "service_name": "fraud-detection",
            "summary": "fraud-detection span attribute completeness < 95%",
            "description": "Only 91% of spans have required service.version attribute.",
        },
    },
}


def build_payload(
    level: str,
    alert_name: str,
    service_name: str,
    summary: str,
    description: str = "",
    status: str = "firing",
) -> dict:
    """Build an AlertManager v4 webhook payload."""
    severity = SEVERITY_MAP[level]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "receiver": "langgraph-claw",
        "status": status,
        "alerts": [{
            "status": status,
            "labels": {
                "alertname": alert_name,
                "severity": severity,
                "service_name": service_name,
            },
            "annotations": {
                "summary": summary,
                "description": description,
            },
            "startsAt": now,
            "endsAt": "",
            "generatorURL": "",
        }],
        "groupLabels": {},
        "commonLabels": {
            "alertname": alert_name,
            "severity": severity,
            "service_name": service_name,
        },
        "commonAnnotations": {
            "summary": summary,
        },
        "externalURL": "",
        "version": "4",
    }


def send_alert(
    base_url: str,
    level: str,
    alert_name: str,
    service_name: str,
    summary: str,
    description: str = "",
    dry_run: bool = False,
) -> bool:
    """Send a single alert to the webhook endpoint."""
    payload = build_payload(level, alert_name, service_name, summary, description)
    url = f"{base_url.rstrip('/')}{WEBHOOK_PATH}"
    data = json.dumps(payload).encode("utf-8")

    if dry_run:
        print(f"[DRY RUN] Would POST to {url}:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return True

    print(f"\U0001f6a8 Triggering {level} alert: {alert_name} on {service_name}")
    print(f"   POST {url}")

    try:
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            print(f"   ✅ Response: {json.dumps(result)}")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"   ❌ HTTP {e.code}: {body}")
        return False
    except urllib.error.URLError as e:
        print(f"   ❌ Connection error: {e.reason}")
        return False


def cmd_single(args: argparse.Namespace) -> None:
    """Handle single alert trigger."""
    success = send_alert(
        args.base_url, args.level, args.alert, args.service,
        args.summary, args.description or "", args.dry_run,
    )
    if not success:
        sys.exit(1)


def cmd_presets(_args: argparse.Namespace) -> None:
    """List available preset alerts."""
    print("Available preset alerts:\n")
    for level in ["P0", "P1", "P2", "P3"]:
        print(f"  [{level}] (severity={SEVERITY_MAP[level]})")
        for name, preset in PRESETS[level].items():
            print(f"    {name}")
            print(f"      Service: {preset['service_name']}")
            print(f"      Summary: {preset['summary']}")
        print()


def cmd_preset_trigger(args: argparse.Namespace) -> None:
    """Trigger a preset alert by name."""
    for level in ["P0", "P1", "P2", "P3"]:
        if args.preset_name in PRESETS[level]:
            preset = PRESETS[level][args.preset_name]
            service = args.service or preset["service_name"]
            summary = args.summary or preset["summary"]
            description = preset.get("description", "")

            success = send_alert(
                args.base_url, level, args.preset_name,
                service, summary, description, args.dry_run,
            )
            if not success:
                sys.exit(1)
            return

    print(f"❌ Preset not found: {args.preset_name}", file=sys.stderr)
    print("   Use 'presets' subcommand to list available presets.", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trigger OTEL alerts via AlertManager webhook endpoint",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Trigger a P0 alert with custom details
  %(prog)s P0 --service frontend --alert ServiceDown --summary "frontend is down"

  # Trigger a P1 alert
  %(prog)s P1 --service checkout --alert HighLatencyP95 --summary "P95 > 800ms"

  # Use a preset (auto-fills service, summary, description)
  %(prog)s preset ServiceDown
  %(prog)s preset HighLatencyP95 --service my-service  # override service

  # List all presets
  %(prog)s presets

  # Batch trigger from JSON file
  %(prog)s batch alerts.json

  # Dry run (print payload without sending)
  %(prog)s P0 --service test --alert TestAlert --summary "test" --dry-run

  # Target a remote server
  %(prog)s --url http://staging:8000 P0 --service frontend --alert ServiceDown --summary "down"
        """,
    )
    parser.add_argument(
        "--url", dest="base_url", default=DEFAULT_BASE_URL,
        help=f"Base URL of langgraph-claw server (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print payload without sending",
    )

    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # ── Single alert subcommands ───────────────────────────────────────
    for level in ["P0", "P1", "P2", "P3"]:
        sp = subparsers.add_parser(level, help=f"Trigger a {level} alert")
        sp.add_argument("--service", required=True, help="Service name")
        sp.add_argument("--alert", required=True, help="Alert name (e.g. ServiceDown)")
        sp.add_argument("--summary", required=True, help="Alert summary")
        sp.add_argument("--description", default="", help="Alert description")
        sp.set_defaults(func=cmd_single, level=level)

    # ── Presets ───────────────────────────────────────────────────────
    presets_parser = subparsers.add_parser("presets", help="List available presets")
    presets_parser.set_defaults(func=cmd_presets)

    preset_parser = subparsers.add_parser("preset", help="Trigger a preset alert")
    preset_parser.add_argument("preset_name", help="Preset alert name")
    preset_parser.add_argument("--service", default="", help="Override service name")
    preset_parser.add_argument("--summary", default="", help="Override summary")
    preset_parser.set_defaults(func=cmd_preset_trigger)

    # ── Batch ─────────────────────────────────────────────────────────
    batch_parser = subparsers.add_parser("batch", help="Trigger alerts from JSON file")
    batch_parser.add_argument("file", help="JSON file with alert definitions")
    batch_parser.set_defaults(func=cmd_batch)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


def cmd_batch(args: argparse.Namespace) -> None:
    """Handle batch alert trigger from JSON file."""
    from pathlib import Path

    batch_file = Path(args.file)
    if not batch_file.exists():
        print(f"❌ File not found: {batch_file}", file=sys.stderr)
        sys.exit(1)

    batch = json.loads(batch_file.read_text(encoding="utf-8"))
    alerts = batch if isinstance(batch, list) else batch.get("alerts", [])

    if not alerts:
        print("❌ No alerts found in batch file", file=sys.stderr)
        sys.exit(1)

    success_count = 0
    fail_count = 0
    for i, item in enumerate(alerts):
        print(f"\n[{i + 1}/{len(alerts)}] ", end="")
        ok = send_alert(
            args.base_url, item["level"], item["alert"],
            item["service"], item["summary"],
            item.get("description", ""), args.dry_run,
        )
        if ok:
            success_count += 1
        else:
            fail_count += 1

    print(f"\n{'=' * 50}")
    print(f"Results: {success_count} success, {fail_count} failed, {len(alerts)} total")
    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
