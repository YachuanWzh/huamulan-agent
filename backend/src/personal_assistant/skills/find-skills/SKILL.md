---
name: find-skills
description: Helps users discover and install agent skills when they ask questions like "how do I do X", "find a skill for X", "is there a skill that can...", or express interest in extending capabilities. This skill should be used when the user is looking for functionality that might exist as an installable skill.
scripts:
  - name: search_public_skills
    description: Search public skills and return structured candidates, with stock/finance fallback when the CLI emits no output.
    command: ["python", "scripts/search_public_skills.py", "{query}"]
    params:
      query:
        type: string
        description: Search query, for example stock, china stock, finance, or trading.
        required: true
  - name: install_project_skill_from_github
    description: Install one GitHub skill into this project's personal_assistant skills directory.
    command: ["python", "scripts/install_project_skill.py", "{package_spec}", "{target_dir}"]
    params:
      package_spec:
        type: string
        description: Skill package in owner/repo@skill-name form, for example sugarforever/01coder-agent-skills@china-stock-analysis.
        required: true
      target_dir:
        type: string
        description: Destination skills directory. Keep the default for this project.
        default: ".."
---

# Find Skills

This skill helps discover and install skills from the open agent skills
ecosystem.

## When To Use This Skill

Use this skill when the user:

- Asks "how do I do X" where X might be a common task with an existing skill.
- Says "find a skill for X" or "is there a skill for X".
- Asks whether a specialized capability exists.
- Wants to search for tools, templates, workflows, or agent capabilities.
- Wants a skill installed into this project.

## Project Install Rule

This assistant does not load skills from the Skills CLI global install location.
It loads project skills from the directory that contains this `find-skills`
skill. In the usual backend workspace, that directory is:

```text
src/personal_assistant/skills
```

When the user asks to install a skill into this project, use the
`install_project_skill_from_github` tool with a package spec in this form:

```text
owner/repo@skill-name
```

Example:

```text
sugarforever/01coder-agent-skills@china-stock-analysis
```

Do not use `npx skills add -g -y` for project installs. It installs into a
global or standard-agent location that this assistant may not scan.

The project installer clones the GitHub repository and copies the matching
skill folder into the project skills directory. If it fails with a GitHub
connection error, report that Git/GitHub network or proxy configuration is
blocking the download; do not keep retrying unrelated commands.

## Search Workflow

Use the `search_public_skills` tool to search the public skills ecosystem. Do
not call `npx skills find` through the generic shell tool unless the structured
search tool itself reports an unexpected failure.

```text
search_public_skills(query="stock")
```

Search notes:

- For Chinese domain terms, also search English keywords. For stock requests,
  try `stock`, `china stock`, `finance`, and `trading`; the Chinese query
  `股票` may return no results.
- `npx skills find` does not support `--json`; do not add that flag.
- Do not pipe through Unix-only tools such as `head` on Windows.
- Prefer direct commands with `--yes` so `npx` does not wait for package
  installation prompts.
- If a CLI search returns exit 0 with no output, do not conclude "no matches".
  Some CLI/TUI output is suppressed in non-interactive shell captures. Use the
  structured tool result and its fallback candidates.

## Quality Checks Before Installing

Do not install a skill based solely on a name. Check:

1. Install count: prefer skills with 1K+ installs.
2. Source reputation: known organizations are safer than unknown authors.
3. Repository quality: inspect the GitHub repository when network access allows.

For stock-related requests, good first searches are:

```text
search_public_skills(query="stock")
search_public_skills(query="china stock")
search_public_skills(query="finance")
search_public_skills(query="trading")
```

After choosing a candidate, install it with:

```text
install_project_skill_from_github(package_spec="owner/repo@skill-name")
```

## When No Skills Are Found

If no relevant skills exist:

1. Say that no matching skill was found.
2. Mention the exact search terms that were tried.
3. Offer to help directly with the task using the assistant's general tools.
