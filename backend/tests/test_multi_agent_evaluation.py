import asyncio


from personal_assistant.skills.evaluation import (
    GoldenSkillCase,
)
from personal_assistant.skills.evaluation.offline import evaluate_multi_agent_intent_cases


def make_case(
    id: str,
    query: str,
    intent: str | None = None,
    metrics: list[str] | None = None,
    entities: list[str] | None = None,
) -> GoldenSkillCase:
    return GoldenSkillCase(
        id=id,
        query=query,
        expected_intent=intent,
        expected_metrics=metrics or [],
        expected_entities=entities or [],
    )


class TestMultiAgentIntentEvaluation:
    def test_exact_intent_match_returns_perfect_accuracy(self):
        cases = [
            make_case(
                "m1",
                "排查 checkout API p95 超时并给出 RCA",
                intent="troubleshoot",
                metrics=["p95"],
                entities=["checkout", "api"],
            ),
        ]
        result = evaluate_multi_agent_intent_cases(cases)

        assert result.total_cases == 1
        assert result.intent_accuracy == 1.0


    def test_empty_cases_returns_none_metrics(self):
        result = evaluate_multi_agent_intent_cases([])

        assert result.total_cases == 0
        assert result.intent_accuracy is None

    def test_metric_extraction_recall(self):
        cases = [
            make_case(
                "m3",
                "LCP p95 从 2.4s 涨到 6.7s",
                intent="troubleshoot",
                metrics=["p95", "lcp"],
                entities=["checkout"],
            ),
        ]
        result = evaluate_multi_agent_intent_cases(cases)

        assert result.metric_extraction_recall is not None
        assert result.metric_extraction_recall == 1.0  # both p95 and lcp found

    def test_entity_extraction_recall(self):
        cases = [
            make_case(
                "m4",
                "checkout API 超时，排查 cart_v2 模块问题",
                intent="troubleshoot",
                entities=["checkout", "cart_v2"],
            ),
        ]
        result = evaluate_multi_agent_intent_cases(cases)

        assert result.entity_extraction_recall == 1.0

    def test_no_expected_intent_skips_case(self):
        """Cases without expected_intent are ignored in multi-agent eval."""
        cases = [
            GoldenSkillCase(id="old", query="hello", expected_skills=["weather"]),
        ]
        result = evaluate_multi_agent_intent_cases(cases)

        assert result.total_cases == 0
        assert result.intent_accuracy is None

    def test_wrong_intent_reduces_accuracy(self):
        cases = [
            make_case("m5", "帮我查下指标定义", intent="troubleshoot"),
        ]
        # "帮我查下指标定义" contains "指标" → intent should be "metrics"
        result = evaluate_multi_agent_intent_cases(cases)

        # intent will be metrics (because of keyword match), expected is troubleshoot
        assert result.intent_accuracy == 0.0


class TestQuickCaseMultiAgent:
    def test_run_quick_case_multi_agent_uses_rewrite_query(self, monkeypatch, tmp_path):
        """When agent_mode='multi', _run_quick_case should use rewrite_query_and_slots."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
        monkeypatch.setenv("LLM_MODEL", "test-model")
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost")
        monkeypatch.setenv("LLM_API_KEY", "test-key")

        from personal_assistant.api.server import _run_quick_case
        from personal_assistant.skills import SkillRegistry

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        registry = SkillRegistry(str(skills_dir))

        case = GoldenSkillCase(
            id="m-test",
            query="排查 checkout API p95 超时并给出 RCA",
            expected_intent="troubleshoot",
            expected_metrics=["p95"],
            expected_entities=["checkout"],
        )

        outcome = asyncio.run(
            _run_quick_case(registry, case, agent_mode="multi")
        )

        assert "intent_slots" in outcome
        assert outcome["intent_slots"]["intent"] == "troubleshoot"
        assert outcome["selected_skills"] == []

    def test_run_quick_case_still_works_for_single_agent(self, monkeypatch, tmp_path):
        """Single-agent mode should still use route_skill_names_with_trace."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
        monkeypatch.setenv("LLM_MODEL", "test-model")
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost")
        monkeypatch.setenv("LLM_API_KEY", "test-key")

        from personal_assistant.api.server import _run_quick_case
        from personal_assistant.skills import SkillRegistry

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        registry = SkillRegistry(str(skills_dir))

        case = GoldenSkillCase(
            id="s-test",
            query="今天天气怎么样",
            expected_skills=["weather"],
        )

        outcome = asyncio.run(
            _run_quick_case(registry, case, agent_mode="single")
        )

        assert "selected_skills" in outcome
        assert "routing_trace" in outcome
        assert "intent_slots" not in outcome


class TestMultiAgentDiagnostics:
    def test_build_detail_for_multi_agent_checks_intent(self):
        from personal_assistant.agent.multi_agent import rewrite_query_and_slots
        from personal_assistant.skills.evaluation.diagnostics import (
            build_case_evaluation_detail,
        )

        case = GoldenSkillCase(
            id="m-diag-1",
            query="排查 checkout API p95 超时",
            expected_intent="troubleshoot",
            expected_metrics=["p95"],
            expected_entities=["checkout", "api"],
        )
        payload = rewrite_query_and_slots(case.query)
        outcome = {
            "case": case,
            "selected_skills": [],
            "intent_slots": payload["slots"],
            "rewritten_query": payload["rewritten_query"],
            "logs": [],
            "final_answer": "",
            "tool_names": [],
            "tool_calls": [],
            "tool_completed": False,
            "tool_failed": False,
        }

        detail = build_case_evaluation_detail(case, outcome, mode="quick")

        check_names = [c.name for c in detail.checks]
        assert "intent_match" in check_names

    def test_build_detail_intent_match_passes(self):
        from personal_assistant.skills.evaluation.diagnostics import (
            build_case_evaluation_detail,
        )

        case = GoldenSkillCase(
            id="m-diag-2",
            query="帮我排查线上故障",
            expected_intent="troubleshoot",
        )
        outcome = {
            "case": case,
            "selected_skills": [],
            "intent_slots": {
                "intent": "troubleshoot",
                "domain": "apm",
                "metrics": [],
                "entities": [],
                "requires_user_vector_context": True,
            },
            "logs": [],
            "final_answer": "",
            "tool_names": [],
            "tool_calls": [],
            "tool_completed": False,
            "tool_failed": False,
        }

        detail = build_case_evaluation_detail(case, outcome, mode="quick")

        intent_check = next(c for c in detail.checks if c.name == "intent_match")
        assert intent_check.passed is True

    def test_build_detail_intent_mismatch_fails(self):
        from personal_assistant.skills.evaluation.diagnostics import (
            build_case_evaluation_detail,
        )

        case = GoldenSkillCase(
            id="m-diag-3",
            query="帮我查下指标",
            expected_intent="troubleshoot",
        )
        outcome = {
            "case": case,
            "selected_skills": [],
            "intent_slots": {
                "intent": "metrics",
                "domain": "apm",
                "metrics": [],
                "entities": [],
                "requires_user_vector_context": True,
            },
            "logs": [],
            "final_answer": "",
            "tool_names": [],
            "tool_calls": [],
            "tool_completed": False,
            "tool_failed": False,
        }

        detail = build_case_evaluation_detail(case, outcome, mode="quick")

        intent_check = next(c for c in detail.checks if c.name == "intent_match")
        assert intent_check.passed is False

    def test_multi_agent_status_pass_when_intent_matches(self):
        """Multi-agent: status should be PASS when intent matches, ignoring skill checks."""
        from personal_assistant.skills.evaluation.diagnostics import (
            build_case_evaluation_detail,
        )

        case = GoldenSkillCase(
            id="m-status-1",
            query="排查 checkout API p95 超时",
            expected_skills=["apm-metrics", "troubleshoot"],  # single-agent skills
            expected_intent="troubleshoot",
            expected_metrics=["p95"],
        )
        outcome = {
            "case": case,
            "selected_skills": [],  # multi-agent has no skill selection
            "intent_slots": {
                "intent": "troubleshoot",
                "domain": "apm",
                "metrics": ["p95"],
                "entities": [],
                "requires_user_vector_context": True,
            },
            "logs": [],
            "final_answer": "",
            "tool_names": [],
            "tool_calls": [],
            "tool_completed": False,
            "tool_failed": False,
        }

        detail = build_case_evaluation_detail(case, outcome, mode="quick")

        # Should NOT have skill checks since intent_slots is present
        skill_check_names = [c.name for c in detail.checks if "skill" in c.name]
        assert len(skill_check_names) == 0, f"Unexpected skill checks: {skill_check_names}"

        # Intent matches → status should be "pass"
        assert detail.status == "pass", f"Expected pass, got {detail.status}: {detail.diagnosis}"

    def test_multi_agent_status_fail_when_intent_mismatches(self):
        """Multi-agent: status should be FAIL when intent mismatches."""
        from personal_assistant.skills.evaluation.diagnostics import (
            build_case_evaluation_detail,
        )

        case = GoldenSkillCase(
            id="m-status-2",
            query="帮我查下指标定义",
            expected_intent="troubleshoot",
        )
        outcome = {
            "case": case,
            "selected_skills": [],
            "intent_slots": {
                "intent": "metrics",
                "domain": "apm",
                "metrics": [],
                "entities": [],
                "requires_user_vector_context": True,
            },
            "logs": [],
            "final_answer": "",
            "tool_names": [],
            "tool_calls": [],
            "tool_completed": False,
            "tool_failed": False,
        }

        detail = build_case_evaluation_detail(case, outcome, mode="quick")

        # Intent mismatch → status should be "fail"
        assert detail.status == "fail"
