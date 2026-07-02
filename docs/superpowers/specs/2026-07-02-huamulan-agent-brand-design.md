# huamulan-agent Brand Design

## Goal

Rename and reframe the project as `huamulan-agent`: a single LangGraph agent persona inspired by Hua Mulan, while preserving the current secure ReAct assistant architecture.

## Scope

- Frontend: update visible product naming, core console copy, and the CSS visual system.
- README: update the top-level positioning and add an extension roadmap.
- Extension items: document proposals only; do not implement backend skills, plugins, or new APIs.

## Frontend Direction

The UI remains an operational console, not a decorative landing page. The visual identity should feel like a war-room desk, campaign ledger, and tack-room inventory: practical, disciplined, and ready for repeated work.

Palette:
- Iron Armor `#171717`
- Jujube Red `#8f2d2d`
- Saddle Leather `#8a5a35`
- Rice Paper `#f7f1e3`
- Bronze Green `#34675c`

Signature element: a restrained "four markets kit rail" in the header: `东市 骏马 / 西市 鞍鞯 / 南市 辔头 / 北市 长鞭`. It signals the Mulan brief without turning the console into a costume.

## README Direction

The README should identify the product as `huamulan-agent`, a secure single-agent LangGraph workbench. Existing engineering capabilities remain relevant: ReAct loop, human approval, audit logs, hot-pluggable skills, memory, context compaction, cache, checkpointing, and observability.

## Extension Roadmap

Document these as future options only:

- East Market / Mount: task intake, objective clarification, and work readiness checks.
- West Market / Saddle: context packaging, dependency gathering, and tool choice.
- South Market / Reins: approval policy, guardrails, and execution control.
- North Market / Whip: verification, audit, replay, and handoff reporting.

## Testing

- Frontend tests should assert the new name and visible four-market rail.
- CSS tests should assert the named Mulan palette tokens and avoid generic decorative pseudo-elements.
- README should be checked by content tests or direct command output to confirm the new name and roadmap wording.
