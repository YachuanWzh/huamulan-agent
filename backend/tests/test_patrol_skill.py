# -*- coding: utf-8 -*-
"""Tests for the patrol skill — Kafka consumption + alert posting."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the patrol script is importable
SKILLS_DIR = Path(__file__).resolve().parents[1] / "src" / "personal_assistant" / "skills"
sys.path.insert(0, str(SKILLS_DIR / "patrol" / "scripts"))


class TestAnomalyToAlertLevel:
    """Tests for anomaly_to_alert_level function."""

    def test_high_severity_maps_to_P2(self):
        from run_patrol import anomaly_to_alert_level
        assert anomaly_to_alert_level("high") == "P2"

    def test_medium_severity_maps_to_P3(self):
        from run_patrol import anomaly_to_alert_level
        assert anomaly_to_alert_level("medium") == "P3"

    def test_unknown_severity_defaults_to_P3(self):
        from run_patrol import anomaly_to_alert_level
        assert anomaly_to_alert_level("unknown") == "P3"
        assert anomaly_to_alert_level("") == "P3"


class TestBuildAlertPayload:
    """Tests for build_alert_payload function."""

    def test_builds_alert_manager_webhook_format(self):
        from run_patrol import build_alert_payload
        payload = build_alert_payload(
            service_name="test-svc",
            alert_name="P95LatencyAnomaly",
            summary="P95 latency anomaly detected: 1500ms",
            level="P2",
            description="IQR upper bound: 800ms, actual: 1500ms",
        )
        assert payload["receiver"] == "langgraph-claw"
        assert payload["status"] == "firing"
        assert payload["version"] == "4"
        assert len(payload["alerts"]) == 1
        alert = payload["alerts"][0]
        assert alert["labels"]["severity"] == "info"  # P2 -> info
        assert alert["labels"]["alertname"] == "P95LatencyAnomaly"
        assert alert["labels"]["service_name"] == "test-svc"
        assert alert["annotations"]["summary"] == "P95 latency anomaly detected: 1500ms"

    def test_p3_maps_to_none_severity(self):
        from run_patrol import build_alert_payload
        payload = build_alert_payload(
            service_name="svc",
            alert_name="TestAlert",
            summary="summary",
            level="P3",
        )
        assert payload["alerts"][0]["labels"]["severity"] == "none"

    def test_starts_at_is_iso8601_utc(self):
        from run_patrol import build_alert_payload
        payload = build_alert_payload(
            service_name="svc",
            alert_name="TestAlert",
            summary="summary",
            level="P2",
        )
        starts_at = payload["alerts"][0]["startsAt"]
        assert "T" in starts_at
        assert starts_at.endswith("Z")


class TestPostAlerts:
    """Tests for post_alerts function."""

    def test_posts_to_otel_alerts_endpoint(self):
        from run_patrol import build_alert_payload, post_alerts
        payloads = [
            build_alert_payload("svc1", "Alert1", "summary1", "P2"),
            build_alert_payload("svc2", "Alert2", "summary2", "P3"),
        ]
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"status": "accepted", "alerts": 1}'
        mock_response.status = 200
        # urlopen is used as a context manager: `with urlopen(...) as resp`
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_response

        with patch("urllib.request.urlopen", return_value=mock_cm):
            results = post_alerts(payloads, server_url="http://localhost:8000")
            assert len(results) == 2
            assert all(r["success"] for r in results)

    def test_handles_http_error(self):
        from run_patrol import build_alert_payload, post_alerts
        from urllib.error import HTTPError

        mock_error = HTTPError("http://localhost:8000", 500, "Error", {}, None)

        payloads = [build_alert_payload("svc", "Alert", "summary", "P2")]
        with patch("urllib.request.urlopen", side_effect=mock_error):
            results = post_alerts(payloads, server_url="http://localhost:8000")
            assert len(results) == 1
            assert not results[0]["success"]
            assert "error" in results[0]


class TestRunPatrol:
    """Tests for run_patrol function."""

    def test_returns_empty_when_no_kafka_messages(self):
        from run_patrol import run_patrol

        mock_consumer = MagicMock()
        mock_consumer.consume_and_analyze.return_value = []
        with patch(
            "personal_assistant.consumers.kafka_consumer.OtelKafkaConsumer",
            return_value=mock_consumer,
        ):
            result = run_patrol(window="5m", topic="test", limit=10)
            assert result == {"status": "ok", "alerts_posted": 0, "traces_consumed": 0,
                              "messages_consumed": 0, "anomalies_detected": 0,
                              "metric_alerts_detected": 0, "errors": []}

    def test_posts_alerts_for_snapshots_with_anomalies(self):
        from run_patrol import run_patrol
        from personal_assistant.apm import (
            AnomalySignal,
            BackendObservabilitySummary,
            FrontendObservabilitySummary,
            ObservabilitySnapshot,
            RootCauseReport,
        )
        snapshot = ObservabilitySnapshot(
            frontend=FrontendObservabilitySummary(
                total_events=5,
                error_count=1,
                resource_error_count=0,
                web_vitals={},
                top_errors=[],
            ),
            backend=BackendObservabilitySummary(
                total_events=3,
                tool_errors=0,
                tool_retries=0,
                p95_duration_ms=None,
            ),
            anomalies=[
                AnomalySignal(
                    metric="LCP",
                    value=5000.0,
                    method="iqr",
                    severity="high",
                    reason="LCP value 5000 is above IQR upper bound 3000",
                ),
            ],
            root_cause=RootCauseReport(
                category="frontend_performance",
                summary="Frontend performance metrics crossed APM thresholds.",
                evidence=[],
                recommendation="Inspect render-blocking resources.",
            ),
        )
        mock_consumer = MagicMock()
        mock_consumer.consume_and_analyze.return_value = [snapshot]
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"status": "accepted", "alerts": 1}'
        mock_response.status = 200
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_response

        with patch(
            "personal_assistant.consumers.kafka_consumer.OtelKafkaConsumer",
            return_value=mock_consumer,
        ):
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = run_patrol(
                    window="5m", topic="test", limit=10,
                    server_url="http://localhost:8000",
                )
                assert result["status"] == "ok"
                assert result["alerts_posted"] == 1
                assert result["traces_consumed"] == 1

    def test_skips_snapshots_without_anomalies(self):
        from run_patrol import run_patrol
        from personal_assistant.apm import (
            BackendObservabilitySummary,
            FrontendObservabilitySummary,
            ObservabilitySnapshot,
            RootCauseReport,
        )
        snapshot = ObservabilitySnapshot(
            frontend=FrontendObservabilitySummary(
                total_events=5, error_count=1, resource_error_count=0,
                web_vitals={}, top_errors=[],
            ),
            backend=BackendObservabilitySummary(
                total_events=3, tool_errors=0, tool_retries=0,
                p95_duration_ms=None,
            ),
            anomalies=[],  # No anomalies
            root_cause=RootCauseReport(
                category="normal",
                summary="No dominant incident signature.",
                evidence=[],
                recommendation="Keep collecting.",
            ),
        )
        mock_consumer = MagicMock()
        mock_consumer.consume_and_analyze.return_value = [snapshot]

        with patch(
            "personal_assistant.consumers.kafka_consumer.OtelKafkaConsumer",
            return_value=mock_consumer,
        ):
            result = run_patrol(window="5m", topic="test", limit=10)
            assert result["alerts_posted"] == 0
            assert result["traces_consumed"] == 1

    def test_posts_metric_alerts_from_otlp_metrics_topic(self):
        from run_patrol import run_patrol

        metric_alert = {
            "level": "P2",
            "service_name": "test-alert-generator",
            "alert_name": "LatencyTrendRising",
            "summary": "test-alert-generator P95 latency rising trend detected",
            "description": "P95 latency histogram indicates rising trend.",
        }

        mock_alert_consumer = MagicMock()
        mock_alert_consumer._fetch_alert_messages.return_value = [b"otlp"]
        mock_trace_consumer = MagicMock()
        mock_trace_consumer.consume_and_analyze.return_value = []
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"status": "accepted", "alerts": 1}'
        mock_response.status = 200
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_response

        with patch(
            "personal_assistant.consumers.alert_consumer.AlertKafkaConsumer",
            return_value=mock_alert_consumer,
        ):
            with patch(
                "personal_assistant.consumers.alert_consumer._parse_otlp_metrics",
                return_value=[metric_alert],
            ):
                with patch(
                    "personal_assistant.consumers.kafka_consumer.OtelKafkaConsumer",
                    return_value=mock_trace_consumer,
                ):
                    with patch("urllib.request.urlopen", return_value=mock_cm) as mock_urlopen:
                        result = run_patrol(window="5m", topic="otlp_metrics", limit=10)

        assert result["status"] == "ok"
        assert result["alerts_posted"] == 1
        assert result["metric_alerts_detected"] == 1
        posted_payload = json.loads(mock_urlopen.call_args.args[0].data.decode("utf-8"))
        alert = posted_payload["alerts"][0]
        assert alert["labels"]["severity"] == "info"
        assert alert["labels"]["alertname"] == "LatencyTrendRising"
        assert alert["labels"]["service_name"] == "test-alert-generator"

    def test_deduplicates_repeated_metric_alerts_in_one_patrol_run(self):
        from run_patrol import run_patrol

        metric_alert = {
            "level": "P2",
            "service_name": "test-alert-generator",
            "alert_name": "LatencyTrendRising",
            "summary": "test-alert-generator P95 latency rising trend detected",
            "description": "P95 latency histogram indicates rising trend.",
        }

        mock_alert_consumer = MagicMock()
        mock_alert_consumer._fetch_alert_messages.return_value = [b"one", b"two"]
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"status": "accepted", "alerts": 1}'
        mock_response.status = 200
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_response

        with patch(
            "personal_assistant.consumers.alert_consumer.AlertKafkaConsumer",
            return_value=mock_alert_consumer,
        ):
            with patch(
                "personal_assistant.consumers.alert_consumer._parse_otlp_metrics",
                return_value=[metric_alert],
            ):
                with patch("urllib.request.urlopen", return_value=mock_cm) as mock_urlopen:
                    result = run_patrol(window="5m", topic="otlp_metrics", limit=10)

        assert result["messages_consumed"] == 2
        assert result["metric_alerts_detected"] == 2
        assert result["alerts_posted"] == 1
        assert mock_urlopen.call_count == 1


class TestPatrolAgentSkills:
    """Verify patrol skill is mounted on patrol_agent in multi_agent.py."""

    def test_patrol_agent_has_patrol_skill(self):
        from personal_assistant.agent.multi_agent import CHILD_AGENT_SKILLS
        assert "patrol" in CHILD_AGENT_SKILLS
        patrol_skills = CHILD_AGENT_SKILLS["patrol"]
        assert "patrol" in patrol_skills, (
            f"Expected 'patrol' skill in patrol agent skills, got: {patrol_skills}"
        )

    def test_patrol_agent_has_otel_query_skill(self):
        """Patrol agent should also have otel-query for on-demand data fetching."""
        from personal_assistant.agent.multi_agent import CHILD_AGENT_SKILLS
        patrol_skills = CHILD_AGENT_SKILLS["patrol"]
        assert "otel-query" in patrol_skills, (
            f"Expected 'otel-query' skill in patrol agent skills, got: {patrol_skills}"
        )

    def test_patrol_is_registered_subagent(self):
        from personal_assistant.agent.multi_agent import APM_SUBAGENTS
        assert "patrol" in APM_SUBAGENTS

    def test_patrol_skill_default_limit_covers_metrics_tail(self):
        skill_md = SKILLS_DIR / "patrol" / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        assert "default: 500" in text


class TestTriggerNonInterference:
    """Verify patrol skill triggers do NOT match queries that belong to other skills."""

    PATROL_TRIGGERS = {
        "patrol", "inspection", "巡检", "自动巡检",
        "定时巡检", "批量巡检", "执行巡检", "拉取预警", "扫描预警",
    }

    def test_patrol_triggers_dont_match_knowledge_queries(self):
        """Queries asking about metric definitions should NOT trigger patrol."""
        knowledge_queries = [
            "什么是LCP",
            "怎么定义error rate",
            "Apdex是什么含义",
            "error budget怎么解读",
            "Web Vitals采集方法",
            "how to collect custom metrics",
        ]
        for query in knowledge_queries:
            lower = query.lower()
            matched = any(t in lower for t in self.PATROL_TRIGGERS)
            assert not matched, (
                f"Knowledge query '{query}' should NOT match patrol triggers, "
                f"but matched: {[t for t in self.PATROL_TRIGGERS if t in lower]}"
            )

    def test_patrol_triggers_dont_match_troubleshoot_queries(self):
        """Queries asking to diagnose incidents should NOT trigger patrol."""
        troubleshoot_queries = [
            "排查这个超时问题",
            "根因分析一下",
            "RCA这个故障",
            "帮我定位这个异常",
            "troubleshoot the frontend error",
            "APM incident diagnosis",
        ]
        for query in troubleshoot_queries:
            lower = query.lower()
            matched = any(t in lower for t in self.PATROL_TRIGGERS)
            assert not matched, (
                f"Troubleshoot query '{query}' should NOT match patrol triggers, "
                f"but matched: {[t for t in self.PATROL_TRIGGERS if t in lower]}"
            )

    def test_patrol_triggers_dont_match_otel_query_queries(self):
        """Queries asking to query specific traces/metrics should NOT trigger patrol."""
        otel_queries = [
            "查一下最近的trace",
            "查一下jaeger的span",
            "查一下Prometheus的latency",
            "查看error rate的metric",
            "show me spans for frontend service",
        ]
        for query in otel_queries:
            lower = query.lower()
            matched = any(t in lower for t in self.PATROL_TRIGGERS)
            assert not matched, (
                f"OTEL query '{query}' should NOT match patrol triggers, "
                f"but matched: {[t for t in self.PATROL_TRIGGERS if t in lower]}"
            )

    def test_patrol_triggers_dont_match_audit_queries(self):
        """Queries asking to audit execution logs should NOT trigger patrol."""
        audit_queries = [
            "审计这个线程",
            "检查工具调用失败率",
            "查一下审批事件",
            "audit the execution logs",
            "compliance report",
        ]
        for query in audit_queries:
            lower = query.lower()
            matched = any(t in lower for t in self.PATROL_TRIGGERS)
            assert not matched, (
                f"Audit query '{query}' should NOT match patrol triggers, "
                f"but matched: {[t for t in self.PATROL_TRIGGERS if t in lower]}"
            )

    def test_patrol_triggers_DO_match_patrol_queries(self):
        """Actual patrol queries SHOULD match patrol triggers."""
        patrol_queries = [
            ("执行巡检", "执行巡检"),           # exact match
            ("run patrol now", "patrol"),      # English substring
            ("自动巡检所有服务", "自动巡检"),    # 自动巡检 as prefix
            ("设置定时巡检规则", "定时巡检"),    # 定时巡检 as substring
            ("批量巡检任务", "批量巡检"),        # 批量巡检 as prefix
            ("帮我拉取预警信息", "拉取预警"),    # 拉取预警 as substring
            ("扫描预警数据", "扫描预警"),        # 扫描预警 as prefix
            ("patrol inspection", "inspection"), # English substring
        ]
        for query, expected_trigger in patrol_queries:
            lower = query.lower()
            matched = any(t in lower for t in self.PATROL_TRIGGERS)
            assert matched, (
                f"Patrol query '{query}' SHOULD match patrol triggers "
                f"(expected at least '{expected_trigger}'), "
                f"but matched none. Triggers: {self.PATROL_TRIGGERS}"
            )
