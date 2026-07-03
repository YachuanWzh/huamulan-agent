import json
import sys

from personal_assistant.apm import FrontendRumEvent, build_observability_snapshot
from personal_assistant.api.schemas import ExecutionLog


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    rum_events = [FrontendRumEvent.model_validate(item) for item in payload.get("rum_events", [])]
    execution_logs = [
        ExecutionLog.model_validate(item) for item in payload.get("execution_logs", [])
    ]
    snapshot = build_observability_snapshot(rum_events, execution_logs)
    print(snapshot.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
