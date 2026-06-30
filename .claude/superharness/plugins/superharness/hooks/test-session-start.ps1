# Test: session-start.ps1 loads memory files from backend/.memory/
# This test should FAIL initially (RED) - current hook doesn't load memory.

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
# The hook uses [Console]::Out.Write() so we must redirect Console.Out
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

# --- Assertions ---
$failures = @()

# Assertion 1: Memory file content must be present
# The user-preferred-name-yachuan.md contains the CJK chars for user's name
# We read the file to get the exact content to check
$memFile = Join-Path $memoryDir 'user-preferred-name-yachuan.md'
$memContent = Get-Content $memFile -Raw -Encoding UTF8
# Extract first few meaningful chars to match against ctx
# The memory says to call user by a specific name - check for uniqueness
# Since CJK may have encoding issues in match, check for the ASCII part
if ($ctx.Contains('yachuan')) {
    Write-Output "  PASS: Memory content 'yachuan' found in additionalContext"
} else {
    $failures += "FAIL: Memory content 'yachuan' NOT found in additionalContext"
}

# Assertion 2: Memory index (MEMORY.md) content must be present
if ($ctx -match 'Memory Index') {
    Write-Output "  PASS: MEMORY.md index content found in additionalContext"
} else {
    $failures += "FAIL: MEMORY.md index content NOT found in additionalContext"
}

# Assertion 3: SYSTEM.md content must be present
if ($ctx -match 'FastAPI') {
    Write-Output "  PASS: SYSTEM.md content found in additionalContext"
} else {
    $failures += "FAIL: SYSTEM.md content NOT found in additionalContext"
}

# Assertion 4: USER.md content must be present
if ($ctx -match '# User') {
    Write-Output "  PASS: USER.md content found in additionalContext"
} else {
    $failures += "FAIL: USER.md content NOT found in additionalContext"
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
