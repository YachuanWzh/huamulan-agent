import json

import pytest

from personal_assistant.agent.child_agent_protocol import (
    ChildAgentStatus,
    ChildAgentTask,
    SubAgentInput,
    SubAgentReport,
)


class TestSubAgentInput:
    def test_serialize_to_json_payload(self):
        """子 agent 输入序列化为 JSON 时必须包含所有必需字段"""
        inp = SubAgentInput(
            task_id="task-001",
            agent="troubleshoot",
            query="排查 payment-service 超时",
            intent_slots={"intent": "troubleshoot", "metrics": ["p99"]},
            user_vector_context={"documents": []},
        )
        data = inp.model_dump()
        assert data["agent"] == "troubleshoot"
        assert data["task_id"] == "task-001"
        assert "communication_contract" in data
        assert data["communication_contract"]["format"] == "json"

    def test_communication_contract_required_fields(self):
        inp = SubAgentInput(
            task_id="task-001",
            agent="metrics",
            query="查看 p95",
            intent_slots={},
        )
        contract = inp.model_dump()["communication_contract"]
        for field in [
            "agent",
            "task_id",
            "findings",
            "evidence",
            "recommendations",
            "status",
            "confidence",
        ]:
            assert field in contract["required_fields"]


class TestSubAgentReport:
    def test_parse_valid_json(self):
        raw = json.dumps(
            {
                "agent": "troubleshoot",
                "task_id": "task-001",
                "status": "completed",
                "findings": ["DB connection pool exhausted"],
                "evidence": ["pg_stat_activity shows 100/100 connections"],
                "recommendations": ["Increase max_connections to 200"],
                "confidence": 0.92,
                "tools_used": ["query_traces", "query_metrics"],
                "error": None,
            }
        )
        report = SubAgentReport.model_validate_json(raw)
        assert report.agent == "troubleshoot"
        assert report.status == "completed"
        assert len(report.findings) == 1
        assert report.confidence == 0.92

    def test_failed_status_with_error(self):
        raw = json.dumps(
            {
                "agent": "patrol",
                "task_id": "task-002",
                "status": "failed",
                "findings": [],
                "evidence": [],
                "recommendations": [],
                "confidence": 0.0,
                "tools_used": [],
                "error": "Prometheus API unreachable",
            }
        )
        report = SubAgentReport.model_validate_json(raw)
        assert report.status == "failed"
        assert report.error == "Prometheus API unreachable"

    def test_default_values(self):
        report = SubAgentReport(
            agent="audit",
            task_id="task-003",
            status="completed",
        )
        assert report.findings == []
        assert report.evidence == []
        assert report.recommendations == []
        assert report.confidence == 0.5
        assert report.tools_used == []
        assert report.error is None

    def test_rejects_invalid_status(self):
        raw = json.dumps(
            {
                "agent": "metrics",
                "task_id": "task-004",
                "status": "running",  # 不允许 — 只有 completed/failed
                "findings": [],
                "evidence": [],
                "recommendations": [],
                "confidence": 0.5,
            }
        )
        with pytest.raises(Exception):  # ValidationError
            SubAgentReport.model_validate_json(raw)


class TestChildAgentTask:
    def test_lifecycle_pending_to_completed(self):
        task = ChildAgentTask(
            task_id="task-005",
            agent_name="troubleshoot",
            status=ChildAgentStatus.PENDING,
        )
        assert task.status == ChildAgentStatus.PENDING
        assert task.started_at is None

        task.status = ChildAgentStatus.RUNNING
        task.started_at = "2026-07-07T10:00:00Z"
        assert task.status == ChildAgentStatus.RUNNING

        task.status = ChildAgentStatus.COMPLETED
        task.completed_at = "2026-07-07T10:00:05Z"
        assert task.status == ChildAgentStatus.COMPLETED

    def test_failed_status(self):
        task = ChildAgentTask(
            task_id="task-006",
            agent_name="metrics",
            status=ChildAgentStatus.FAILED,
            error="Tool execution timeout",
        )
        assert task.status == ChildAgentStatus.FAILED
        assert task.error == "Tool execution timeout"


class TestAgentStateChildTracking:
    def test_apm_reports_use_sub_agent_report(self):
        """apm_reports 应能容纳 SubAgentReport 对象"""
        report = SubAgentReport(
            agent="troubleshoot",
            task_id="task-001",
            status="completed",
            findings=["f1"],
        )
        data = report.model_dump()
        assert data["agent"] == "troubleshoot"
        assert data["task_id"] == "task-001"
