#!/usr/bin/env python3
"""s15_tracing.py - Execution logger and audit trail for Agent observability."""
import time as time_mod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    TURN = "turn"
    SKILL_ROUTE = "skill_route"
    LLM = "llm"
    TOOL = "tool"
    TOOL_RETRY = "tool_retry"
    APPROVAL = "approval"
    SECURITY = "security"


class EventStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    RETRYING = "retrying"
    APPROVED = "approved"
    DENIED = "denied"


@dataclass
class ExecutionEvent:
    """A single structured log entry."""
    event_type: str
    status: str
    name: str = ""
    duration_ms: int = 0
    input_data: dict | None = None
    output_data: dict | None = None
    error: str | None = None
    token_usage: dict | None = None
    metadata: dict | None = None
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time_mod.time()


@dataclass
class ExecutionLogger:
    """Append-only structured event log for Agent observability."""
    events: list[ExecutionEvent] = field(default_factory=list)

    def log(self, event_type: str, status: str, **kwargs) -> ExecutionEvent:
        event = ExecutionEvent(event_type=event_type, status=status, **kwargs)
        self.events.append(event)
        return event

    def query(self, event_type: str = None, status: str = None) -> list[ExecutionEvent]:
        result = self.events
        if event_type:
            result = [e for e in result if e.event_type == event_type]
        if status:
            result = [e for e in result if e.status == status]
        return result

    def summary(self) -> dict:
        """Aggregate statistics."""
        by_type: dict[str, dict] = {}
        for e in self.events:
            if e.event_type not in by_type:
                by_type[e.event_type] = {"total": 0, "failed": 0, "blocked": 0}
            by_type[e.event_type]["total"] += 1
            if e.status == "failed":
                by_type[e.event_type]["failed"] += 1
            if e.status == "blocked":
                by_type[e.event_type]["blocked"] += 1
        return by_type

    def print_summary(self) -> None:
        s = self.summary()
        print("\n  Execution Summary:")
        print(f"  {'Event Type':<20} {'Total':>6} {'Failed':>6} {'Blocked':>6}")
        print(f"  {'-'*20} {'-'*6} {'-'*6} {'-'*6}")
        for etype in sorted(s):
            data = s[etype]
            print(f"  {etype:<20} {data['total']:>6} {data['failed']:>6} {data['blocked']:>6}")

    def security_events(self) -> list[ExecutionEvent]:
        return self.query(event_type="security")

    def failed_tools(self) -> list[ExecutionEvent]:
        return self.query(event_type="tool", status="failed")


# --- Demo ---
if __name__ == "__main__":
    print("s15: Tracing & Observability - Execution Logger Demo\n")

    logger = ExecutionLogger()

    # Simulate a complete agent interaction
    print("Simulating agent turn...")

    logger.log("turn", "started", name="user_query", input_data={"message": "帮我写个脚本"})

    logger.log("skill_route", "completed", name="route_skills",
               output_data={"matched": [], "method": "regex"})

    logger.log("llm", "started", name="agent_call")
    logger.log("llm", "completed", name="agent_call", duration_ms=1200,
               token_usage={"prompt_tokens": 450, "completion_tokens": 80})

    logger.log("tool", "started", name="write_file",
               input_data={"path": "hello.py", "content": "print('hello')"})

    logger.log("approval", "requested", name="write_file",
               metadata={"approval_id": "appr_001"})

    # User approves
    logger.log("approval", "approved", name="write_file",
               metadata={"approval_id": "appr_001"})

    logger.log("tool", "completed", name="write_file", duration_ms=15,
               output_data={"written": "hello.py"})

    # Simulate a security event
    logger.log("security", "blocked", name="prompt_guard",
               metadata={"category": "instruction_override", "severity": "HIGH"})

    # Simulate a tool failure with retry
    logger.log("tool", "started", name="bash", input_data={"command": "python bad.py"})
    logger.log("tool", "failed", name="bash", error="ModuleNotFoundError: No module named 'xyz'")
    logger.log("tool_retry", "retrying", name="bash", metadata={"attempt": 1, "max_retries": 3})
    logger.log("tool_retry", "completed", name="bash", duration_ms=320,
               output_data={"stdout": "Hello World"})

    logger.log("llm", "started", name="agent_call")
    logger.log("llm", "completed", name="agent_call", duration_ms=800,
               token_usage={"prompt_tokens": 520, "completion_tokens": 120})

    logger.log("turn", "completed", name="user_query", duration_ms=2500)

    # Print results
    logger.print_summary()

    print("\n  Security Events:")
    for e in logger.security_events():
        print(f"    [{e.metadata.get('category', '?')}] {e.metadata.get('severity', '?')}")

    print("\n  Failed Tools (before retry):")
    for e in logger.failed_tools():
        print(f"    {e.name}: {e.error}")

    total_events = len(logger.events)
    total_tokens = sum((e.token_usage or {}).get("total_tokens", 0) for e in logger.events)
    print(f"\n  Total events: {total_events}")
    print(f"  Total tokens used: {total_tokens}")