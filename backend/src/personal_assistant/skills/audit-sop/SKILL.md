---
name: audit-sop
description: Audit SOP skill. Use when the user asks to analyze agent audit logs, inspect a thread execution trace, diagnose tool failures or retry chains, review token usage, explain security or approval events, or generate an audit report for a LangGraph Assistant conversation.
---

# Audit SOP

Use this SOP to analyze execution logs for one conversation thread.

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

Do not invent logs. If required audit data is unavailable, say exactly which data is missing and what endpoint or page should be checked.
