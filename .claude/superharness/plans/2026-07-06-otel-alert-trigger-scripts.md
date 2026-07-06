# OTEL Alert Trigger Scripts Implementation Plan

> **For agentic workers:** Execute this plan task-by-task under the superharness:go workflow, Phase 2 (strict TDD per task). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建命令行脚本，可通过 HTTP POST 调用 `POST /api/otel/alerts` 端点，自动触发 P0/P1/P2/P3 四个级别的 OTEL 告警，用于测试和演示。

**Architecture:** 脚本直接构造 AlertManager Webhook v4 格式的 JSON payload，通过 `curl` 或 Python `requests` 发送到 langgraph-claw 服务。P0/P1 走已有的即时 RCA 通道，P2/P3 需要先让服务端点支持存储和 SSE 广播（但不触发 RCA）。

**Tech Stack:** Python 3.12 (click/argparse), Bash, PowerShell — 纯 HTTP 客户端，无额外依赖。

---

## 背景分析

### 现有告警入口

- **端点**: `POST /api/otel/alerts` (已实现，见 `backend/src/personal_assistant/api/server.py:490-552`)
- **Schema**: `AlertManagerWebhook` + `OtelAlert` (见 `backend/src/personal_assistant/api/schemas.py:205-238`)
- **P0 (severity=critical)**: 自动触发后台 RCA → `troubleshoot_agent`
- **P1 (severity=warning)**: 存储 + SSE 广播，不自动 RCA
- **P2/P3**: **当前被静默丢弃**（代码 L507: `if severity not in ("critical", "warning"): continue`）

### AlertManager Webhook 格式

```json
{
  "receiver": "langgraph-claw",
  "status": "firing",
  "alerts": [{
    "status": "firing",
    "labels": {
      "alertname": "ServiceDown",
      "severity": "critical",
      "service_name": "frontend"
    },
    "annotations": {
      "summary": "frontend service is down",
      "description": "up == 0 for 1 minute"
    },
    "startsAt": "2026-07-06T10:00:00Z",
    "endsAt": "",
    "generatorURL": ""
  }],
  "groupLabels": {},
  "commonLabels": {},
  "commonAnnotations": {},
  "externalURL": "",
  "version": "4"
}
```

### 定级规则（参考 otel.md）

| 级别 | severity 标签 | 典型场景 |
|------|-------------|---------|
| P0 | `critical` | 服务不可用、5xx > 50%、P95 > SLO×5、DB连接池耗尽 |
| P1 | `warning` | P95 > SLO×2、错误率 > 5%、断路器打开 |
| P2 | `info` | 延迟趋势上升、慢查询增多、内存泄漏早期信号 |
| P3 | `none` | 日常巡检、资源水位、SLO 合规、span 属性完整性 |

---

### Task 1: 修改服务端支持 P2/P3 告警

**Files:**
- Modify: `backend/src/personal_assistant/api/server.py:490-552`

- [ ] **Step 1: 修改 handle_otel_alert 让 P2/P3 也入库**

将 severity 检查从"丢弃非 P0/P1"改为"仅存储 + SSE 广播，不触发 RCA"。

当前代码 (L505-508):
```python
        severity = alert.labels.get("severity", "")
        if severity not in ("critical", "warning"):
            continue  # P2/P3 come via Kafka
```

改为:
```python
        severity = alert.labels.get("severity", "")

        # Map severity label to display level
        _SEVERITY_LEVEL_MAP = {
            "critical": "P0",
            "warning": "P1",
            "info": "P2",
            "none": "P3",
        }
        level = _SEVERITY_LEVEL_MAP.get(severity, severity.upper() if severity else "P3")
```

同时修改后续的 `"level"` 赋值 (L529):
```python
            "level": level,  # was: "P0" if severity == "critical" else "P1"
```

以及 P0-only RCA 触发 (L545):
```python
        # P0 (critical): auto-trigger RCA in background with auto-approval
        if level == "P0":
            task = asyncio.create_task(_trigger_rca_background(alert_data))
            _active_rca_tasks.add(task)
            task.add_done_callback(_active_rca_tasks.discard)
```

- [ ] **Step 2: 运行现有测试确认未破坏已有功能**

```bash
cd backend && python -m pytest src/personal_assistant/ -x -q --timeout=30 2>&1 | tail -5
```

Expected: 所有已有测试通过。

- [ ] **Step 3: 手动验证端点接受 P2/P3**

启动 server 后测试:
```bash
curl -s -X POST http://localhost:8000/api/otel/alerts \
  -H "Content-Type: application/json" \
  -d '{"receiver":"test","status":"firing","alerts":[{"status":"firing","labels":{"alertname":"TestP2","severity":"info","service_name":"test-svc"},"annotations":{"summary":"P2 test alert"},"startsAt":"2026-07-06T10:00:00Z","endsAt":"","generatorURL":""}],"groupLabels":{},"commonLabels":{},"commonAnnotations":{},"externalURL":"","version":"4"}' | python -m json.tool
```

Expected: `{"status": "accepted", "alerts": 1}`

```bash
curl -s http://localhost:8000/api/otel/alerts/history | python -m json.tool
```

Expected: 最近一条是 level=P2 的告警。

- [ ] **Step 4: Commit**

```bash
git add backend/src/personal_assistant/api/server.py
git commit -m "feat(otel): accept P2/P3 alerts via webhook (store + SSE, no RCA)

P2 (severity=info) and P3 (severity=none) alerts are now accepted at
POST /api/otel/alerts, stored in the in-memory alert deque, and
broadcast via SSE. Only P0 (critical) triggers auto-RCA.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: 创建 Python CLI 告警触发脚本

**Files:**
- Create: `backend/scripts/trigger_otel_alert.py`

- [ ] **Step 1: 编写脚本**

```python
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
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BASE_URL = "http://localhost:8000"
WEBHOOK_PATH = "/api/otel/alerts"

# ── Severity to level mapping ──────────────────────────────────────────
SEVERITY_MAP = {
    "P0": "critical",
    "P1": "warning",
    "P2": "info",
    "P3": "none",
}

# ── Preset alerts for each level ───────────────────────────────────────
PRESETS = {
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


def build_payload(level: str, alert_name: str, service_name: str,
                  summary: str, description: str = "",
                  status: str = "firing") -> dict:
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


def send_alert(base_url: str, level: str, alert_name: str,
               service_name: str, summary: str, description: str = "",
               dry_run: bool = False) -> bool:
    """Send a single alert to the webhook endpoint."""
    payload = build_payload(level, alert_name, service_name, summary, description)
    url = f"{base_url.rstrip('/')}{WEBHOOK_PATH}"
    data = json.dumps(payload).encode("utf-8")

    if dry_run:
        print(f"[DRY RUN] Would POST to {url}:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return True

    print(f"🚨 Triggering {level} alert: {alert_name} on {service_name}")
    print(f"   POST {url}")

    try:
        req = urllib.request.Request(
            url, data=data,
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


def cmd_single(args):
    """Handle single alert trigger."""
    success = send_alert(
        args.base_url, args.level, args.alert, args.service,
        args.summary, args.description or "", args.dry_run,
    )
    if not success:
        sys.exit(1)


def cmd_batch(args):
    """Handle batch alert trigger from JSON file."""
    batch_file = Path(args.file)
    if not batch_file.exists():
        print(f"❌ File not found: {batch_file}")
        sys.exit(1)

    batch = json.loads(batch_file.read_text(encoding="utf-8"))
    alerts = batch if isinstance(batch, list) else batch.get("alerts", [])

    if not alerts:
        print("❌ No alerts found in batch file")
        sys.exit(1)

    success_count = 0
    fail_count = 0
    for i, item in enumerate(alerts):
        print(f"\n[{i+1}/{len(alerts)}] ", end="")
        ok = send_alert(
            args.base_url, item["level"], item["alert"],
            item["service"], item["summary"],
            item.get("description", ""), args.dry_run,
        )
        if ok:
            success_count += 1
        else:
            fail_count += 1

    print(f"\n{'='*50}")
    print(f"Results: {success_count} success, {fail_count} failed, {len(alerts)} total")
    if fail_count > 0:
        sys.exit(1)


def cmd_presets(_args):
    """List available preset alerts."""
    print("Available preset alerts:\n")
    for level in ["P0", "P1", "P2", "P3"]:
        print(f"  [{level}] (severity={SEVERITY_MAP[level]})")
        for name, preset in PRESETS[level].items():
            print(f"    {name}")
            print(f"      Service: {preset['service_name']}")
            print(f"      Summary: {preset['summary']}")
        print()


def cmd_preset_trigger(args):
    """Trigger a preset alert by name."""
    # Find the preset across all levels
    for level in ["P0", "P1", "P2", "P3"]:
        if args.preset_name in PRESETS[level]:
            preset = PRESETS[level][args.preset_name]
            overrides = {}
            if args.service:
                overrides["service_name"] = args.service
            if args.summary:
                overrides["summary"] = args.summary

            success = send_alert(
                args.base_url, level, args.preset_name,
                overrides.get("service_name", preset["service_name"]),
                overrides.get("summary", preset["summary"]),
                preset.get("description", ""),
                args.dry_run,
            )
            if not success:
                sys.exit(1)
            return

    print(f"❌ Preset not found: {args.preset_name}")
    print("   Use 'presets' subcommand to list available presets.")
    sys.exit(1)


def main():
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

    # ── Single alert ──────────────────────────────────────────────────
    single_parser = subparsers.add_parser("P0", help="Trigger a P0 (critical) alert")
    single_parser.add_argument("--service", required=True, help="Service name")
    single_parser.add_argument("--alert", required=True, help="Alert name (e.g. ServiceDown)")
    single_parser.add_argument("--summary", required=True, help="Alert summary")
    single_parser.add_argument("--description", default="", help="Alert description")
    single_parser.set_defaults(func=cmd_single, level="P0")

    for level in ["P1", "P2", "P3"]:
        p = subparsers.add_parser(level, help=f"Trigger a {level} alert")
        p.add_argument("--service", required=True, help="Service name")
        p.add_argument("--alert", required=True, help="Alert name")
        p.add_argument("--summary", required=True, help="Alert summary")
        p.add_argument("--description", default="", help="Alert description")
        p.set_defaults(func=cmd_single, level=level)

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


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 测试脚本**

```bash
# Test --help
python backend/scripts/trigger_otel_alert.py --help

# Test presets listing
python backend/scripts/trigger_otel_alert.py presets

# Test dry-run (server doesn't need to be running)
python backend/scripts/trigger_otel_alert.py --dry-run P0 --service test --alert TestAlert --summary "test"
```

Expected: 输出 JSON payload，不报错。

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/trigger_otel_alert.py
git commit -m "feat(otel): add CLI script to trigger P0-P3 alerts via webhook

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: 创建快速 Shell 脚本（便捷包装）

**Files:**
- Create: `backend/scripts/trigger_p0.sh`
- Create: `backend/scripts/trigger_p1.sh`
- Create: `backend/scripts/trigger_p2.sh`
- Create: `backend/scripts/trigger_p3.sh`

每个脚本使用 curl 快速触发对应级别的告警，减少输入。

- [ ] **Step 1: 编写 trigger_p0.sh**

```bash
#!/usr/bin/env bash
# Trigger a P0 (critical) OTEL alert
# Usage: ./trigger_p0.sh <service_name> <alert_name> <summary>
# Example: ./trigger_p0.sh frontend ServiceDown "frontend is down"

set -euo pipefail

SERVICE="${1:-frontend}"
ALERT="${2:-ServiceDown}"
SUMMARY="${3:-${SERVICE} is DOWN — critical alert}"
BASE_URL="${OTEL_BASE_URL:-http://localhost:8000}"

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "🚨 Triggering P0 (critical) alert: ${ALERT} on ${SERVICE}"

curl -s -X POST "${BASE_URL}/api/otel/alerts" \
  -H "Content-Type: application/json" \
  -d @- <<EOF | python3 -m json.tool
{
  "receiver": "langgraph-claw",
  "status": "firing",
  "alerts": [{
    "status": "firing",
    "labels": {
      "alertname": "${ALERT}",
      "severity": "critical",
      "service_name": "${SERVICE}"
    },
    "annotations": {
      "summary": "${SUMMARY}"
    },
    "startsAt": "${NOW}",
    "endsAt": "",
    "generatorURL": ""
  }],
  "groupLabels": {},
  "commonLabels": {
    "alertname": "${ALERT}",
    "severity": "critical",
    "service_name": "${SERVICE}"
  },
  "commonAnnotations": {
    "summary": "${SUMMARY}"
  },
  "externalURL": "",
  "version": "4"
}
EOF
```

trigger_p1.sh / trigger_p2.sh / trigger_p3.sh 同理，仅替换 severity 值。

- [ ] **Step 2: 测试 shell 脚本**

```bash
# 确认脚本可执行
chmod +x backend/scripts/trigger_p*.sh

# Dry-run by pointing at a non-existent server (should get connection error, not script error)
# Or just check syntax:
bash -n backend/scripts/trigger_p0.sh && echo "P0 script OK"
bash -n backend/scripts/trigger_p1.sh && echo "P1 script OK"
bash -n backend/scripts/trigger_p2.sh && echo "P2 script OK"
bash -n backend/scripts/trigger_p3.sh && echo "P3 script OK"
```

Expected: 所有脚本语法检查通过。

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/trigger_p0.sh backend/scripts/trigger_p1.sh \
        backend/scripts/trigger_p2.sh backend/scripts/trigger_p3.sh
git commit -m "feat(otel): add quick shell scripts for P0-P3 alert triggering

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: 创建 PowerShell 脚本（Windows 用户）

**Files:**
- Create: `backend/scripts/trigger_otel_alert.ps1`

- [ ] **Step 1: 编写多功能 PowerShell 脚本**

```powershell
<#
.SYNOPSIS
  Trigger OTEL alerts via the AlertManager webhook endpoint (PowerShell).

.DESCRIPTION
  Sends AlertManager v4-format webhook payloads to POST /api/otel/alerts.
  Supports P0 (critical), P1 (warning), P2 (info), P3 (none) severity levels.

.PARAMETER Level
  Alert level: P0, P1, P2, or P3.

.PARAMETER Service
  Service name (e.g., frontend, checkout, cart).

.PARAMETER Alert
  Alert name (e.g., ServiceDown, HighLatencyP95).

.PARAMETER Summary
  Human-readable alert summary.

.PARAMETER Description
  Optional detailed description.

.PARAMETER BaseUrl
  Base URL of the langgraph-claw server (default: http://localhost:8000).

.PARAMETER DryRun
  Print the JSON payload without sending.

.EXAMPLE
  .\trigger_otel_alert.ps1 P0 -Service frontend -Alert ServiceDown -Summary "frontend is down"

.EXAMPLE
  .\trigger_otel_alert.ps1 P2 -Service recommendation -Alert RpsSurge -Summary "RPS +300%"

.EXAMPLE
  .\trigger_otel_alert.ps1 P3 -Service accounting -Alert ErrorBudget -Summary "error budget < 50%"

.EXAMPLE
  .\trigger_otel_alert.ps1 P0 -Service test -Alert TestAlert -Summary "test" -DryRun
#>

param(
    [Parameter(Mandatory=$true, Position=0)]
    [ValidateSet("P0", "P1", "P2", "P3")]
    [string]$Level,

    [Parameter(Mandatory=$true)]
    [string]$Service,

    [Parameter(Mandatory=$true)]
    [string]$Alert,

    [Parameter(Mandatory=$true)]
    [string]$Summary,

    [string]$Description = "",

    [string]$BaseUrl = "http://localhost:8000",

    [switch]$DryRun
)

$severityMap = @{
    "P0" = "critical"
    "P1" = "warning"
    "P2" = "info"
    "P3" = "none"
}

$severity = $severityMap[$Level]
$now = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

$payload = @{
    receiver = "langgraph-claw"
    status = "firing"
    alerts = @(
        @{
            status = "firing"
            labels = @{
                alertname = $Alert
                severity = $severity
                service_name = $Service
            }
            annotations = @{
                summary = $Summary
                description = $Description
            }
            startsAt = $now
            endsAt = ""
            generatorURL = ""
        }
    )
    groupLabels = @{}
    commonLabels = @{
        alertname = $Alert
        severity = $severity
        service_name = $Service
    }
    commonAnnotations = @{
        summary = $Summary
    }
    externalURL = ""
    version = "4"
}

$json = $payload | ConvertTo-Json -Depth 5 -Compress
$prettyJson = $payload | ConvertTo-Json -Depth 5

if ($DryRun) {
    Write-Host "[DRY RUN] Would POST to $BaseUrl/api/otel/alerts:" -ForegroundColor Yellow
    Write-Host $prettyJson
    return
}

$url = "$BaseUrl/api/otel/alerts"
Write-Host "🚨 Triggering $Level ($severity) alert: $Alert on $Service" -ForegroundColor Red
Write-Host "   POST $url"

try {
    $response = Invoke-RestMethod -Uri $url -Method Post -Body $json -ContentType "application/json" -TimeoutSec 10
    Write-Host "   ✅ Response: $($response | ConvertTo-Json -Compress)" -ForegroundColor Green
} catch {
    Write-Host "   ❌ Error: $_" -ForegroundColor Red
    exit 1
}
```

- [ ] **Step 2: 测试语法**

```powershell
# Check syntax
powershell -NoProfile -Command "Get-Command backend/scripts/trigger_otel_alert.ps1 -Syntax"
```

Expected: 无解析错误。

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/trigger_otel_alert.ps1
git commit -m "feat(otel): add PowerShell script for P0-P3 alert triggering

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 创建批量告警示例文件

**Files:**
- Create: `backend/scripts/alerts_batch_example.json`

- [ ] **Step 1: 编写批量告警 JSON**

```json
{
  "description": "Batch alert trigger example — use with: python trigger_otel_alert.py batch alerts_batch_example.json",
  "alerts": [
    {
      "level": "P0",
      "alert": "ServiceDown",
      "service": "frontend",
      "summary": "frontend service is DOWN — up == 0 for 1 minute"
    },
    {
      "level": "P1",
      "alert": "HighLatencyP95",
      "service": "checkout",
      "summary": "checkout P95 latency 800ms > SLO×2"
    },
    {
      "level": "P2",
      "alert": "SlowQueryIncrease",
      "service": "product-catalog",
      "summary": "product-catalog DB slow queries increasing"
    },
    {
      "level": "P3",
      "alert": "ErrorBudgetBurnRate",
      "service": "ad",
      "summary": "ad service error budget remaining < 50%"
    }
  ]
}
```

- [ ] **Step 2: Commit**

```bash
git add backend/scripts/alerts_batch_example.json
git commit -m "docs(otel): add batch alert trigger example JSON

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 使用方式汇总

### 快速触发（服务器运行后）

```bash
# === Python (推荐，功能最全) ===

# 列出所有预设告警
python backend/scripts/trigger_otel_alert.py presets

# 使用预设触发（一条命令搞定）
python backend/scripts/trigger_otel_alert.py preset ServiceDown
python backend/scripts/trigger_otel_alert.py preset HighLatencyP95
python backend/scripts/trigger_otel_alert.py preset SlowQueryIncrease
python backend/scripts/trigger_otel_alert.py preset ErrorBudgetBurnRate

# 自定义参数
python backend/scripts/trigger_otel_alert.py P0 --service my-svc --alert CrashLoop --summary "pod crash looping"

# 批量触发
python backend/scripts/trigger_otel_alert.py batch backend/scripts/alerts_batch_example.json

# === Bash (简洁) ===

./backend/scripts/trigger_p0.sh frontend ServiceDown "frontend is down"
./backend/scripts/trigger_p1.sh checkout HighLatencyP95 "P95 > 800ms"

# === PowerShell (Windows) ===

.\backend\scripts\trigger_otel_alert.ps1 P0 -Service frontend -Alert ServiceDown -Summary "frontend is down"
.\backend\scripts\trigger_otel_alert.ps1 P1 -Service checkout -Alert HighLatencyP95 -Summary "P95 > 800ms"
```

---

## 自检

### 1. Spec 覆盖
- ✅ P0 告警触发 (Python / Bash / PowerShell)
- ✅ P1 告警触发
- ✅ P2 告警触发
- ✅ P3 告警触发
- ✅ 批量触发
- ✅ 预设告警库
- ✅ 服务端 P2/P3 兼容

### 2. Placeholder 扫描
- ✅ 无 TBD/TODO
- ✅ 所有代码步骤包含实际代码

### 3. 类型一致性
- ✅ Severity mapping 在 Python/Bash/PS 中一致
- ✅ AlertManager v4 格式在三个脚本中一致
