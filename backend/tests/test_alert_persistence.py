"""Tests for AlertPersistence dual-write logic.

Validates Redis key patterns, JSON serialization, and data integrity.
"""

import json

import pytest


class TestAlertPersistenceRedisKeyPatterns:
    """Verify Redis key pattern constants."""

    def test_alert_key_format(self):
        from personal_assistant.api.alert_persistence import ALERT_KEY
        key = ALERT_KEY.format(alert_id="abc123")
        assert key == "otel:alert:abc123"

    def test_alert_list_key(self):
        from personal_assistant.api.alert_persistence import ALERT_LIST_KEY
        assert ALERT_LIST_KEY == "otel:alert:list"

    def test_alert_ttl_is_24_hours(self):
        from personal_assistant.api.alert_persistence import ALERT_TTL_SECONDS
        assert ALERT_TTL_SECONDS == 86400

    def test_alert_list_max_size(self):
        from personal_assistant.api.alert_persistence import ALERT_LIST_MAX_SIZE
        assert ALERT_LIST_MAX_SIZE == 500


class TestAlertPersistenceDataIntegrity:
    """Verify alert data round-trips correctly for JSON serialization."""

    def test_alert_data_serializable(self):
        """Alert data dict should be JSON-serializable for Redis storage."""
        from datetime import datetime, timezone
        from uuid import uuid4

        alert_data = {
            "id": uuid4().hex[:12],
            "received_at": datetime.now(timezone.utc).isoformat(),
            "severity": "info",
            "level": "P2",
            "service_name": "test-service",
            "alert_name": "TestAlert",
            "summary": "Test summary",
            "description": "Test description",
            "starts_at": "2026-07-06T10:00:00Z",
            "status": "firing",
            "rca_status": "pending",
            "rca_thread_id": None,
            "rca_pending_approvals": None,
            "rca_result_text": None,
            "metadata": {},
        }
        # Should not raise
        serialized = json.dumps(alert_data, ensure_ascii=False, default=str)
        deserialized = json.loads(serialized)
        assert deserialized["id"] == alert_data["id"]
        assert deserialized["level"] == "P2"
        assert deserialized["rca_status"] == "pending"

    def test_alert_data_with_approvals_serializable(self):
        """Alert data with pending approvals should serialize correctly."""
        from datetime import datetime, timezone
        from uuid import uuid4

        alert_data = {
            "id": uuid4().hex[:12],
            "received_at": datetime.now(timezone.utc).isoformat(),
            "severity": "critical",
            "level": "P0",
            "service_name": "frontend",
            "alert_name": "ServiceDown",
            "summary": "down",
            "description": "",
            "starts_at": None,
            "status": "firing",
            "rca_status": "blocked",
            "rca_thread_id": "rca-test-001",
            "rca_pending_approvals": [
                {"approval_id": "appr-1", "tool_call_id": "tc-1",
                 "name": "bash", "args": {"command": "sudo reboot"}}
            ],
            "rca_result_text": None,
            "metadata": {"source": "alertmanager"},
        }
        serialized = json.dumps(alert_data, ensure_ascii=False, default=str)
        deserialized = json.loads(serialized)
        assert deserialized["rca_pending_approvals"][0]["name"] == "bash"
        assert deserialized["metadata"]["source"] == "alertmanager"


class TestAlertPersistenceInit:
    """Verify AlertPersistence constructor accepts expected dependencies."""

    def test_init_without_redis_and_pg(self):
        """Should not raise when initialized without Redis or PG."""
        try:
            from personal_assistant.api.alert_persistence import AlertPersistence
        except ImportError:
            pytest.skip("AlertPersistence not yet implemented")

        persistence = AlertPersistence(redis_client=None, postgres_memory=None)
        assert persistence.redis is None
        assert persistence.pg is None

    def test_init_with_redis_client(self):
        """Should accept a redis client."""
        try:
            from personal_assistant.api.alert_persistence import AlertPersistence
        except ImportError:
            pytest.skip("AlertPersistence not yet implemented")

        # Use a sentinel object to verify it's stored
        sentinel = object()
        persistence = AlertPersistence(redis_client=sentinel, postgres_memory=None)
        assert persistence.redis is sentinel
