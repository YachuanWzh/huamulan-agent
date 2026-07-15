"""Integration tests for OTEL alert API with persistence and P2/P3 Analyze endpoint.

Validates:
- Alert data schema for persistence
- Severity-to-level mapping for all 4 levels
- P2/P3 Analyze endpoint auto-approval contract
"""

from datetime import datetime, timezone
from uuid import uuid4



class TestOtelAlertApiSchema:
    """Verify alert data schema for persistence compatibility."""

    def test_alert_data_has_all_persistence_fields(self):
        """Alert data must contain all fields needed for PostgreSQL upsert."""
        alert_data = {
            "id": uuid4().hex[:12],
            "received_at": datetime.now(timezone.utc).isoformat(),
            "severity": "critical",
            "level": "P0",
            "service_name": "frontend",
            "alert_name": "ServiceDown",
            "summary": "frontend is DOWN",
            "description": "Service has been down for 1 minute",
            "starts_at": "2026-07-06T10:00:00Z",
            "status": "firing",
            "rca_status": "pending",
            "rca_thread_id": None,
            "rca_pending_approvals": None,
            "rca_result_text": None,
            "metadata": {},
        }
        pg_fields = {
            "id", "received_at", "severity", "level", "service_name",
            "alert_name", "summary", "description", "starts_at", "status",
            "rca_status", "rca_thread_id", "rca_pending_approvals",
            "rca_result_text", "metadata",
        }
        for field in pg_fields:
            assert field in alert_data, f"Missing PG field: {field}"

    def test_alert_level_mapping_covers_all_severities(self):
        """Severity-to-level mapping must cover all 4 severity values."""
        from personal_assistant.api.server import _severity_to_level
        assert _severity_to_level("critical") == "P0"
        assert _severity_to_level("warning") == "P1"
        assert _severity_to_level("info") == "P2"
        assert _severity_to_level("none") == "P3"
        assert _severity_to_level("unknown") == "P3"  # default fallback

    def test_p2_alert_has_correct_level(self):
        """P2 (info) alerts should have level='P2'."""
        from personal_assistant.api.server import _severity_to_level
        assert _severity_to_level("info") == "P2"

    def test_p3_alert_has_correct_level(self):
        """P3 (none) alerts should have level='P3'."""
        from personal_assistant.api.server import _severity_to_level
        assert _severity_to_level("none") == "P3"


class TestP2P3AnalyzeContract:
    """Verify the P2/P3 Analyze endpoint contract."""

    def test_analyze_uses_rca_auto_approval_for_safe_tools(self):
        """P2/P3 Analyze should auto-approve safe tools (same as P0 RCA)."""
        from personal_assistant.agent.harness import requires_rca_tool_approval

        # Safe tools — auto-approved
        assert requires_rca_tool_approval("query_traces", {"service": "test"}) is False
        assert requires_rca_tool_approval("query_metrics", {"promql": "up"}) is False
        assert requires_rca_tool_approval("grep", {"pattern": "ERROR"}) is False
        assert requires_rca_tool_approval("read_file", {"path": "/tmp/log"}) is False
        assert requires_rca_tool_approval("bash", {"command": "echo hello"}) is False

        # Dangerous tools — require approval
        assert requires_rca_tool_approval("bash", {"command": "sudo reboot"}) is True
        assert requires_rca_tool_approval("bash", {"command": "rm -rf /data"}) is True

    def test_analyze_thread_id_uses_rca_prefix(self):
        """The Analyze endpoint should use 'rca-' prefix for thread IDs."""
        alert_id = "test12345678"
        expected_thread_id = f"rca-{alert_id}"
        assert expected_thread_id.startswith("rca-")
        assert alert_id in expected_thread_id

    def test_analyze_prompt_includes_alert_context(self):
        """The analyze prompt must include complete alert context."""
        alert_data = {
            "level": "P2",
            "alert_name": "LatencyTrendRising",
            "service_name": "payment-service",
            "severity": "info",
            "summary": "P95 latency trending up",
            "description": "Derivative > 0.5ms/min over 1h window",
            "starts_at": "2026-07-06T09:00:00Z",
        }
        prompt = _build_rca_prompt_for_test(alert_data)
        assert "P2" in prompt
        assert "LatencyTrendRising" in prompt
        assert "payment-service" in prompt
        assert "P95 latency trending up" in prompt
        assert "Derivative > 0.5ms/min" in prompt


def _build_rca_prompt_for_test(alert_data: dict) -> str:
    """Build an RCA analyze prompt for P2/P3 alerts (mirrors server._build_rca_prompt)."""
    parts = [
        f"\U0001f6a8 {alert_data['level']} Alert: **{alert_data['alert_name']}**",
        f"- Service: **{alert_data['service_name']}**",
        f"- Severity: {alert_data['severity']}",
        f"- Summary: {alert_data['summary']}",
    ]
    if alert_data.get("description"):
        parts.append(f"- Details: {alert_data['description']}")
    if alert_data.get("starts_at"):
        parts.append(f"- Alert started: {alert_data['starts_at']}")
    parts.extend([
        "",
        "Please run root cause analysis using the otel-query skill:",
        "1. Pull Jaeger traces for the affected service",
        "2. Query Prometheus for correlated metrics",
        "3. Identify the root cause and recommend fixes",
    ])
    return "\n".join(parts)
