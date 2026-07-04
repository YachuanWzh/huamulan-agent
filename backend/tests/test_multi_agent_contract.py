from personal_assistant.api.schemas import ChatRequest, SkillEvaluationRunRequest


def test_chat_request_defaults_to_single_agent_mode() -> None:
    request = ChatRequest(thread_id="t1", message="hello")

    assert request.agent_mode == "single"


def test_chat_request_accepts_multi_agent_mode() -> None:
    request = ChatRequest(thread_id="t1", message="排查 p95 异常", agent_mode="multi")

    assert request.agent_mode == "multi"


def test_skill_evaluation_request_defaults_to_single_agent_mode() -> None:
    request = SkillEvaluationRunRequest(golden_path="apm_realistic")

    assert request.agent_mode == "single"


def test_skill_evaluation_request_accepts_multi_agent_mode() -> None:
    request = SkillEvaluationRunRequest(
        golden_path="apm_realistic",
        evaluation_mode="e2e",
        agent_mode="multi",
    )

    assert request.agent_mode == "multi"
