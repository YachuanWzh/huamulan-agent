# Skill Evaluation Trend Chart Design

## Goal

Show Skill Evaluation history as visual trends, not only a textual history list.

## Design

Each skill scorecard shows a compact SVG sparkline for recent `overall_score` history. The existing History area becomes an expandable trend detail. When expanded, it shows a larger SVG chart with `overall`, `routing`, `runtime`, and `static` score lines when those metrics exist.

The chart consumes the existing `/api/skills/evaluation/history?skill_name=...` response. Scores are normalized to `0..1` before rendering so older percent-scale data remains readable.

## UI Rules

- Card sparkline: recent overall score trend, last 10 points, no heavy axes.
- Expanded detail: multi-line chart, legend, latest delta, and a compact list of recent runs.
- Empty state: one historical point shows "Need at least 2 runs".
- Use existing colors and card surfaces; no new chart library.

## Tests

Cover rendering of the card sparkline, expanded multi-metric trend chart, score normalization, and insufficient-history empty state in `WorkspacePanel.test.tsx`.
