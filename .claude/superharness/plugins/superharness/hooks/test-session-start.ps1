# Test: session-start.ps1 loads memory files from backend/.memory/

$ErrorActionPreference = 'Stop'

# Determine paths
$scriptDir = Split-Path -Parent $PSCommandPath
$pluginRoot = Split-Path -Parent $scriptDir
$projectRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $pluginRoot)))

Write-Output "Test: SessionStart hook injects memory content"
Write-Output "  Project root: $projectRoot"
Write-Output "  Plugin root:  $pluginRoot"

# Verify test fixtures exist
$memoryDir = Join-Path $projectRoot 'backend\.memory'
if (-not (Test-Path $memoryDir)) {
    throw "FAIL: Test fixture missing - $memoryDir does not exist"
}
Write-Output "  Memory dir:   $memoryDir"

# Set env variable for the hook
$env:CLAUDE_PLUGIN_ROOT = $pluginRoot

# Run the hook and capture console output
$hookScript = Join-Path $scriptDir 'session-start.ps1'
Write-Output "  Running: $hookScript"

$sw = New-Object System.IO.StringWriter
$oldOut = [Console]::Out
try {
    [Console]::SetOut($sw)
    & $hookScript
    $output = $sw.ToString()
} finally {
    [Console]::SetOut($oldOut)
    $sw.Dispose()
}

if (-not $output) {
    throw "FAIL: Hook produced no output"
}

# Parse the JSON
try {
    $json = $output | ConvertFrom-Json
} catch {
    throw "FAIL: Hook output is not valid JSON: $_`nOutput: $output"
}

$ctx = $json.hookSpecificOutput.additionalContext
if (-not $ctx) {
    throw "FAIL: additionalContext is empty or missing"
}

# Extract just the MEMORY block for targeted assertions
$memoryBlock = ''
if ($ctx -match '(?s)<MEMORY>(.*?)</MEMORY>') {
    $memoryBlock = $matches[1]
}

# --- Assertions ---
$failures = @()

# Assertion 1: Memory file content must be present within MEMORY block
if ($memoryBlock -and $memoryBlock -match 'yachuan') {
    Write-Output "  PASS: Memory content 'yachuan' found in MEMORY block"
} else {
    $failures += "FAIL: Memory content 'yachuan' NOT found in MEMORY block"
}

# Assertion 2: Memory index (MEMORY.md) content must be present within MEMORY block
if ($memoryBlock -and $memoryBlock -match 'Memory Index') {
    Write-Output "  PASS: MEMORY.md index content found in MEMORY block"
} else {
    $failures += "FAIL: MEMORY.md index content NOT found in MEMORY block"
}

# Assertion 3: SYSTEM.md must be loaded within MEMORY block (hook injects "### System Context" label)
if ($memoryBlock -and $memoryBlock -match '### System Context') {
    Write-Output "  PASS: SYSTEM.md section found in MEMORY block"
} else {
    $failures += "FAIL: SYSTEM.md section NOT found in MEMORY block"
}

# Assertion 4: USER.md must be loaded within MEMORY block (hook injects "### User Context" label)
if ($memoryBlock -and $memoryBlock -match '### User Context') {
    Write-Output "  PASS: USER.md section found in MEMORY block"
} else {
    $failures += "FAIL: USER.md section NOT found in MEMORY block"
}

# Assertion 5: HARNESS.md content still present (memory must not replace harness)
if ($ctx -match 'superharness') {
    Write-Output "  PASS: HARNESS.md content still present (not replaced)"
} else {
    $failures += "FAIL: HARNESS.md content missing - memory loading may have replaced it"
}

# Report
if ($failures.Count -eq 0) {
    Write-Output "`nALL TESTS PASSED"
    exit 0
} else {
    Write-Output "`nFAILURES ($($failures.Count)):"
    $failures | ForEach-Object { Write-Output "  $_" }
    exit 1
}
