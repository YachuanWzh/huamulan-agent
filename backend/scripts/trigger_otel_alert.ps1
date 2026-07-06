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
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("P0", "P1", "P2", "P3")]
    [string]$Level,

    [Parameter(Mandatory = $true)]
    [string]$Service,

    [Parameter(Mandatory = $true)]
    [string]$Alert,

    [Parameter(Mandatory = $true)]
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
    receiver    = "langgraph-claw"
    status      = "firing"
    alerts      = @(
        @{
            status      = "firing"
            labels      = @{
                alertname    = $Alert
                severity     = $severity
                service_name = $Service
            }
            annotations = @{
                summary     = $Summary
                description = $Description
            }
            startsAt    = $now
            endsAt      = ""
            generatorURL = ""
        }
    )
    groupLabels      = @{}
    commonLabels     = @{
        alertname    = $Alert
        severity     = $severity
        service_name = $Service
    }
    commonAnnotations = @{
        summary = $Summary
    }
    externalURL      = ""
    version          = "4"
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
