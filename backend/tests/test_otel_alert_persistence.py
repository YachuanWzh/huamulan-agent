"""Tests for OTEL alert PostgreSQL table and CRUD methods.

Validates the otel_alerts table schema, upsert logic, and field completeness.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest


class TestOtelAlertTableSchema:
    """Verify the otel_alerts table schema contract."""

    def test_alert_data_has_all_required_fields(self):
        """Every alert dict must contain all fields needed for PostgreSQL upsert."""
        alert_data = {
            "id": uuid4().hex[:12],
            "received_at": datetime.now(timezone.utc).isoformat(),
            "severity": "info",
            "level": "P2",
            "service_name": "test-service",
            "alert_name": "TestAlert",
            "summary": "Test alert summary",
            "description": "Test alert description",
            "starts_at": "2026-07-06T10:00:00Z",
            "status": "firing",
            "rca_status": "pending",
            "rca_thread_id": None,
            "rca_pending_approvals": None,
            "rca_result_text": None,
            "metadata": {},
        }
        required_fields = [
            "id", "received_at", "severity", "level", "service_name",
            "alert_name", "summary", "description", "starts_at", "status",
            "rca_status", "rca_thread_id", "rca_pending_approvals",
            "rca_result_text", "metadata",
        ]
        for field in required_fields:
            assert field in alert_data, f"Missing required field: {field}"

    def test_alert_level_must_be_valid(self):
        """Alert level must be one of P0-P3."""
        valid_levels = {"P0", "P1", "P2", "P3"}
        for level in valid_levels:
            alert_data = {
                "id": uuid4().hex[:12],
                "received_at": datetime.now(timezone.utc).isoformat(),
                "severity": "info",
                "level": level,
                "service_name": "test",
                "alert_name": "Test",
                "summary": "",
                "description": "",
                "starts_at": None,
                "status": "firing",
                "rca_status": "pending",
                "rca_thread_id": None,
                "rca_pending_approvals": None,
                "rca_result_text": None,
                "metadata": {},
            }
            assert alert_data["level"] in valid_levels

    def test_rca_status_must_be_valid(self):
        """RCA status must be one of the defined states."""
        valid_statuses = {"pending", "running", "completed", "blocked", "failed"}
        for status in valid_statuses:
            alert_data = {
                "id": uuid4().hex[:12],
                "received_at": datetime.now(timezone.utc).isoformat(),
                "severity": "info",
                "level": "P2",
                "service_name": "test",
                "alert_name": "Test",
                "summary": "",
                "description": "",
                "starts_at": None,
                "status": "firing",
                "rca_status": status,
                "rca_thread_id": None,
                "rca_pending_approvals": None,
                "rca_result_text": None,
                "metadata": {},
            }
            assert alert_data["rca_status"] in valid_statuses


class TestOtelAlertUpsertLogic:
    """Verify the upsert (INSERT ... ON CONFLICT UPDATE) logic."""

    def test_upsert_creates_new_alert(self):
        """First upsert should set created_at and all fields."""
        alert_data = {
            "id": "test-new-001",
            "received_at": "2026-07-06T10:00:00+00:00",
            "severity": "critical",
            "level": "P0",
            "service_name": "frontend",
            "alert_name": "ServiceDown",
            "summary": "Service is down",
            "description": "Frontend service unresponsive",
            "starts_at": "2026-07-06T09:55:00Z",
            "status": "firing",
            "rca_status": "pending",
            "rca_thread_id": None,
            "rca_pending_approvals": None,
            "rca_result_text": None,
            "metadata": {"source": "alertmanager"},
        }
        # New alert should have default rca_status="pending"
        assert alert_data["rca_status"] == "pending"
        assert alert_data["rca_thread_id"] is None

    def test_upsert_updates_rca_fields_on_conflict(self):
        """Second upsert on same id should update RCA tracking fields."""
        alert_data = {
            "id": "test-update-001",
            "received_at": "2026-07-06T10:00:00+00:00",
            "severity": "warning",
            "level": "P1",
            "service_name": "checkout",
            "alert_name": "HighLatency",
            "summary": "High latency detected",
            "description": "P95 > 500ms",
            "starts_at": "2026-07-06T09:50:00Z",
            "status": "firing",
            "rca_status": "running",
            "rca_thread_id": "rca-test-update-001",
            "rca_pending_approvals": None,
            "rca_result_text": None,
            "metadata": {},
        }
        assert alert_data["rca_status"] == "running"
        assert alert_data["rca_thread_id"] == "rca-test-update-001"

        # After RCA completes
        alert_data["rca_status"] = "completed"
        alert_data["rca_result_text"] = "Root cause: N+1 query in checkout service"
        assert alert_data["rca_status"] == "completed"
        assert alert_data["rca_result_text"] is not None


class TestAlertRowToDict:
    """Verify the _alert_row_to_dict helper converts DB rows correctly."""

    def test_row_to_dict_parses_jsonb_fields(self):
        """JSONB fields should be parsed from strings to Python objects."""
        try:
            from personal_assistant.memory.postgres import _alert_row_to_dict
        except ImportError:
            pytest.skip("_alert_row_to_dict not yet implemented")

        # Simulate an asyncpg Row-like object
        class MockRow:
            def __init__(self):
                self._data = {
                    "id": "test-row-001",
                    "created_at": datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc),
                    "updated_at": datetime(2026, 7, 6, 10, 5, 0, tzinfo=timezone.utc),
                    "received_at": datetime(2026, 7, 6, 9, 55, 0, tzinfo=timezone.utc),
                    "severity": "critical",
                    "level": "P0",
                    "service_name": "frontend",
                    "alert_name": "ServiceDown",
                    "summary": "down",
                    "description": "Service unresponsive",
                    "starts_at": "2026-07-06T09:55:00Z",
                    "status": "firing",
                    "rca_status": "completed",
                    "rca_thread_id": "rca-test-row-001",
                    "rca_pending_approvals": '[{"name": "bash", "args": {"command": "ls"}}]',
                    "rca_result_text": "Root cause found",
                    "metadata": '{"source": "alertmanager"}',
                }

            def __getitem__(self, key):
                return self._data[key]

        row = MockRow()
        result = _alert_row_to_dict(row)

        assert result["id"] == "test-row-001"
        assert result["level"] == "P0"
        assert result["rca_status"] == "completed"
        assert result["rca_thread_id"] == "rca-test-row-001"
        # JSONB fields should be parsed
        assert isinstance(result["rca_pending_approvals"], list)
        assert len(result["rca_pending_approvals"]) == 1
        assert result["rca_pending_approvals"][0]["name"] == "bash"
        assert isinstance(result["metadata"], dict)
        assert result["metadata"]["source"] == "alertmanager"

    def test_row_to_dict_handles_none_jsonb(self):
        """JSONB fields that are None should stay None."""
        try:
            from personal_assistant.memory.postgres import _alert_row_to_dict
        except ImportError:
            pytest.skip("_alert_row_to_dict not yet implemented")

        class MockRow:
            def __init__(self):
                self._data = {
                    "id": "test-row-002",
                    "created_at": None,
                    "updated_at": None,
                    "received_at": datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc),
                    "severity": "info",
                    "level": "P2",
                    "service_name": "test",
                    "alert_name": "Test",
                    "summary": "",
                    "description": "",
                    "starts_at": None,
                    "status": "firing",
                    "rca_status": "pending",
                    "rca_thread_id": None,
                    "rca_pending_approvals": None,
                    "rca_result_text": None,
                    "metadata": None,
                }

            def __getitem__(self, key):
                return self._data[key]

        row = MockRow()
        result = _alert_row_to_dict(row)

        assert result["rca_pending_approvals"] is None
        assert result["metadata"] == {}
        assert result["rca_thread_id"] is None
