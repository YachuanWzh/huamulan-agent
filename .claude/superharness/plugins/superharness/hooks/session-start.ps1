# Superharness SessionStart hook.
# Reads HARNESS.md from the plugin root and injects it into the session as
# additionalContext, so Claude Code starts every session with the harness rules loaded.
# Always exits 0: a broken hook must never block a session.

$ErrorActionPreference = 'SilentlyContinue'

$pluginRoot = $env:CLAUDE_PLUGIN_ROOT
if (-not $pluginRoot) { $pluginRoot = Split-Path -Parent $PSScriptRoot }

$harnessPath = Join-Path $pluginRoot 'HARNESS.md'
if (-not (Test-Path $harnessPath)) { exit 0 }

$content = Get-Content $harnessPath -Raw -Encoding UTF8
if (-not $content) { exit 0 }

$context = "<EXTREMELY_IMPORTANT>`nYou have superharness. Follow it for all engineering work in this project.`n`n$content`n</EXTREMELY_IMPORTANT>"

# Append the active tech-stack guidance (STACK.md lives at <marketplace root> = pluginRoot\..\..).
$stackPath = Join-Path (Split-Path -Parent (Split-Path -Parent $pluginRoot)) 'STACK.md'
if (Test-Path $stackPath) {
    $stackContent = Get-Content $stackPath -Raw -Encoding UTF8
    if ($stackContent) {
        $context += "`n`n<EXTREMELY_IMPORTANT>`nThis project targets a specific tech stack. Follow this guidance.`n`n$stackContent`n</EXTREMELY_IMPORTANT>"
    }
}

# Append project memory from backend/.memory/ (MEMORY.md index + linked files + SYSTEM.md + USER.md).
# Derive project root via git, with a fallback that walks up from pluginRoot looking for
# .claude/settings.json so the hook degrades gracefully outside a git checkout.
$projectRoot = git rev-parse --show-toplevel 2>$null
if (-not $projectRoot) {
    $projectRoot = $pluginRoot
    while ($projectRoot -and -not (Test-Path (Join-Path $projectRoot '.claude\settings.json'))) {
        $projectRoot = Split-Path -Parent $projectRoot
    }
}
$memoryDir = Join-Path $projectRoot 'backend\.memory'
if (Test-Path $memoryDir) {
    $memoryContext = $null

    # Read MEMORY.md index and any linked memory files
    $memoryIndexPath = Join-Path $memoryDir 'MEMORY.md'
    if (Test-Path $memoryIndexPath) {
        $memoryIndexContent = Get-Content $memoryIndexPath -Raw -Encoding UTF8
        if ($memoryIndexContent) {
            $memoryContext = "`n`n<MEMORY>`n## Project Memory (from backend/.memory/)`n`n$memoryIndexContent`n"

            # Parse MEMORY.md for linked memory files: markdown links like [title](filename.md)
            $linkedFiles = @()
            $linkPattern = '\[([^\]]+)\]\(([^)]+\.md)\)'
            $mMatches = [regex]::Matches($memoryIndexContent, $linkPattern)
            foreach ($m in $mMatches) {
                $linkedFiles += $m.Groups[2].Value
            }

            # Read each linked memory file
            foreach ($file in $linkedFiles) {
                $filePath = Join-Path $memoryDir $file
                if (Test-Path $filePath) {
                    $fileContent = Get-Content $filePath -Raw -Encoding UTF8
                    if ($fileContent) {
                        $memoryContext += "`n---`n$fileContent`n"
                    }
                }
            }
        }
    }

    # Read SYSTEM.md independently of MEMORY.md
    $systemPath = Join-Path $memoryDir 'SYSTEM.md'
    if (Test-Path $systemPath) {
        $systemContent = Get-Content $systemPath -Raw -Encoding UTF8
        if ($systemContent -and $systemContent.Trim() -and $systemContent.Trim() -notmatch '^#\s*System\s*$') {
            if (-not $memoryContext) { $memoryContext = "`n`n<MEMORY>" }
            $memoryContext += "`n### System Context`n$systemContent`n"
        }
    }

    # Read USER.md independently of MEMORY.md
    $userPath = Join-Path $memoryDir 'USER.md'
    if (Test-Path $userPath) {
        $userContent = Get-Content $userPath -Raw -Encoding UTF8
        if ($userContent -and $userContent.Trim() -and $userContent.Trim() -notmatch '^#\s*User\s*$') {
            if (-not $memoryContext) { $memoryContext = "`n`n<MEMORY>" }
            $memoryContext += "`n### User Context`n$userContent`n"
        }
    }

    # Append memory context if anything was loaded
    if ($memoryContext) {
        $memoryContext += "</MEMORY>"
        $context += $memoryContext
    }
}

$payload = @{
    hookSpecificOutput = @{
        hookEventName     = 'SessionStart'
        additionalContext = $context
    }
}

# ConvertTo-Json handles all JSON escaping (quotes, newlines, unicode).
$json = $payload | ConvertTo-Json -Depth 4
[Console]::Out.Write($json)
exit 0
