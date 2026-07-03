---
name: audit-sop
description: Audit and business governance SOP skill. Use when the user asks to analyze agent audit logs, inspect one thread execution trace, diagnose tool failures or retry chains, review token usage, explain security or approval events, aggregate cross-thread governance signals, identify systemic reliability issues, or generate an audit/patrol report for LangGraph Assistant conversations.
---

# Audit SOP

Use this SOP to analyze execution logs for one conversation thread or to perform
cross-thread business governance patrol.

## Procedure

1. Confirm the `thread_id` or ask for it when it is missing.
2. Review the execution summary first: total events, total tokens, prompt tokens, completion tokens, tool calls, tool errors, tool retries, security events, and total duration.
3. Inspect the timeline in chronological order. Keep the analysis grounded in concrete timestamps, event names, statuses, tool call IDs, and error messages.
4. For token usage, identify unusually large LLM calls and explain whether prompt or completion tokens dominate.
5. For tool retry chains, group events by `metadata.tool_call_id`, list every attempt, explain the failure reason, and state whether the chain finally completed or failed.
6. Check approval and security events. Explain what was requested, approved, denied, or blocked, and why it mattered.
7. Compare tool inputs and outputs with the user's original goal. Note mismatches, missing context, invalid arguments, or repeated ineffective calls.
8. Return a concise report with these sections:
   - Summary
   - Evidence
   - Token Usage
   - Tool Retry Analysis
   - Security And Approval Events
   - Recommendations

## Governance Patrol Upgrade

When the user asks for business governance, patrol, stability review, or
system-wide audit:

1. Aggregate across threads instead of stopping at one conversation. Compare
   retry rate, tool error rate, security block rate, approval denial rate,
   token growth, and p95 duration.
2. Cluster repeated failures by `metadata.tool_call_id`, tool name, error type,
   skill route, and user goal.
3. Identify systemic issues:
   - repeated ineffective tool calls
   - high retry ratio for one dependency
   - recurring approval denials
   - prompt/tool guard hot spots
   - abnormal token expansion
   - low-value skill routing or missing skill coverage
4. If frontend RUM or patrol findings are available, correlate them with agent
   execution logs and call out user-visible impact.
5. Produce a governance report with these additional sections:
   - Cross-thread Signals
   - Systemic Risks
   - Business Impact
   - Owner And Priority
   - Remediation Backlog
   - Verification Plan

Do not invent logs. If required audit data is unavailable, say exactly which data is missing and what endpoint or page should be checked.
