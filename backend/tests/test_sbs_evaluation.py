import json

import pytest
from pydantic import ValidationError

from personal_assistant.memory.postgres import PostgresMemory
from personal_assistant.skills.evaluation.sbs import (
    SBSCandidate,
    SBSCandidateRunConfig,
    SBSReview,
    SBSRunRequest,
    SBSTask,
    canonical_winner,
    export_sbs_jsonl,
    present_blinded_task,
)


def _task() -> SBSTask:
    return SBSTask(
        task_id="sbs-1",
        prompt="诊断接口超时",
        candidate_a=SBSCandidate(candidate_id="model-v1", output="baseline answer"),
        candidate_b=SBSCandidate(candidate_id="model-v2", output="candidate answer"),
        provenance={"baseline_run_id": "run-a", "candidate_run_id": "run-b"},
    )


def test_blinded_task_hides_identity_and_maps_winner() -> None:
    blinded = present_blinded_task(_task(), seed=1)

    payload = blinded.model_dump_json()
    assert "model-v1" not in payload
    assert "model-v2" not in payload
    assert "run-a" not in payload
    review = SBSReview(
        task_id="sbs-1",
        reviewer="alice",
        winner="A",
        reason="A 更准确",
        dimension_scores={"correctness": 5},
    )
    assert canonical_winner(review, blinded) in {"model-v1", "model-v2"}


def test_both_bad_requires_reason_and_scores_are_one_to_five() -> None:
    with pytest.raises(ValidationError):
        SBSReview(task_id="sbs-1", reviewer="alice", winner="both_bad", reason="")
    with pytest.raises(ValidationError):
        SBSReview(
            task_id="sbs-1",
            reviewer="alice",
            winner="tie",
            reason="same",
            dimension_scores={"correctness": 6},
        )


def test_export_sbs_jsonl_is_ordered_and_keeps_provenance() -> None:
    first = _task()
    second = _task().model_copy(update={"task_id": "sbs-2"})

    lines = export_sbs_jsonl([second, first]).strip().splitlines()

    assert [json.loads(line)["task_id"] for line in lines] == ["sbs-1", "sbs-2"]
    assert json.loads(lines[0])["provenance"]["baseline_run_id"] == "run-a"


class _Cursor:
    async def fetchall(self):
        return []


class _Connection:
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _Cursor()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Pool:
    def __init__(self, conn) -> None:
        self.conn = conn

    def connection(self):
        return self.conn


async def test_sbs_tables_preserve_review_revisions() -> None:
    conn = _Connection()
    memory = PostgresMemory("postgresql://example")
    memory.pool = _Pool(conn)

    await memory._setup_sbs()

    sql = "\n".join(call[0] for call in conn.calls)
    assert "CREATE TABLE IF NOT EXISTS sbs_tasks" in sql
    assert "CREATE TABLE IF NOT EXISTS sbs_reviews" in sql
    assert "UNIQUE (task_id, reviewer, revision)" in sql


async def test_sbs_api_returns_blinded_task_and_canonicalizes_review(monkeypatch) -> None:
    from personal_assistant.api import server

    task = _task()
    reviews = []

    class _Memory:
        async def get_sbs_task(self, task_id):
            return task if task_id == task.task_id else None

        async def record_sbs_review(self, review):
            reviews.append(review)
            return review

    monkeypatch.setattr(server, "memory", _Memory())

    blinded = await server.get_sbs_task("sbs-1")
    review = await server.submit_sbs_review(
        "sbs-1",
        SBSReview(
            task_id="sbs-1",
            reviewer="alice",
            winner="A",
            reason="A 更好",
        ),
    )

    assert "candidate_id" not in blinded.model_dump_json()
    assert review.canonical_winner in {"model-v1", "model-v2"}
    assert reviews == [review]


async def test_reviewed_sbs_task_returns_saved_review_for_read_only_display(monkeypatch) -> None:
    from personal_assistant.api import server

    task = _task().model_copy(update={"status": "reviewed"})
    saved_review = SBSReview(
        task_id=task.task_id,
        reviewer="alice",
        winner="B",
        reason="B 的证据更完整",
        revision=2,
        canonical_winner="model-v2",
    )

    class _Memory:
        async def get_sbs_task(self, task_id):
            return task if task_id == task.task_id else None

        async def get_latest_sbs_review(self, task_id):
            return saved_review if task_id == task.task_id else None

    monkeypatch.setattr(server, "memory", _Memory())

    blinded = await server.get_sbs_task(task.task_id)

    assert blinded.status == "reviewed"
    assert blinded.review is not None
    assert blinded.review.reviewer == "alice"
    assert blinded.review.winner == "B"
    assert blinded.review.reason == "B 的证据更完整"
    assert "canonical_winner" not in blinded.model_dump_json()


async def test_reviewed_sbs_task_rejects_another_review(monkeypatch) -> None:
    from fastapi import HTTPException
    from personal_assistant.api import server

    task = _task().model_copy(update={"status": "reviewed"})

    class _Memory:
        async def get_sbs_task(self, task_id):
            return task if task_id == task.task_id else None

        async def record_sbs_review(self, _review):
            raise AssertionError("reviewed tasks must not create another revision")

    monkeypatch.setattr(server, "memory", _Memory())

    with pytest.raises(HTTPException) as exc_info:
        await server.submit_sbs_review(
            task.task_id,
            SBSReview(
                task_id=task.task_id,
                reviewer="bob",
                winner="A",
                reason="attempted edit",
            ),
        )

    assert exc_info.value.status_code == 409


async def test_sbs_queue_does_not_expose_candidate_identity(monkeypatch) -> None:
    from personal_assistant.api import server

    class _Memory:
        async def list_sbs_tasks(self, limit=100):
            return [_task()]

    monkeypatch.setattr(server, "memory", _Memory())

    queue = await server.list_sbs_tasks()

    payload = json.dumps([item.model_dump(mode="json") for item in queue])
    assert "candidate_a" not in payload
    assert "model-v1" not in payload
    assert "baseline answer" not in payload


async def test_delete_sbs_task_removes_the_task(monkeypatch) -> None:
    from personal_assistant.api import server

    deleted = []

    class _Memory:
        async def delete_sbs_task(self, task_id):
            deleted.append(task_id)
            return True

    monkeypatch.setattr(server, "memory", _Memory())

    response = await server.delete_sbs_task("sbs-1")

    assert response.status_code == 204
    assert deleted == ["sbs-1"]


async def test_delete_sbs_task_returns_404_when_missing(monkeypatch) -> None:
    from fastapi import HTTPException
    from personal_assistant.api import server

    class _Memory:
        async def delete_sbs_task(self, _task_id):
            return False

    monkeypatch.setattr(server, "memory", _Memory())

    with pytest.raises(HTTPException) as exc_info:
        await server.delete_sbs_task("missing")

    assert exc_info.value.status_code == 404


async def test_sbs_run_executes_two_project_agents_and_persists_outputs(monkeypatch) -> None:
    from personal_assistant.api import server
    from personal_assistant.api.schemas import ChatResponse

    calls = []
    created = []
    approval_policies = []

    class _Memory:
        async def create_sbs_task(self, task):
            created.append(task)

        async def list_thread_trace_ids(self, thread_id, limit=1):
            return [f"trace-{thread_id.rsplit('-', 1)[-1]}"]

        async def list_trace_logs(self, trace_id):
            return []

    class _Harness:
        async def run_user_turn(
            self,
            thread_id,
            message,
            llm_config,
            *,
            agent_mode,
            requires_approval=None,
        ):
            calls.append((thread_id, message, llm_config.model, agent_mode))
            approval_policies.append(requires_approval)
            return ChatResponse(
                thread_id=thread_id,
                status="completed",
                message=f"{agent_mode} / {llm_config.model}",
            )

    monkeypatch.setattr(server, "memory", _Memory())
    monkeypatch.setattr(server, "harness", _Harness())

    task = await server.run_sbs_candidates(
        SBSRunRequest(
            prompt="诊断接口超时",
            candidate_a=SBSCandidateRunConfig(model="model-one", agent_mode="single"),
            candidate_b=SBSCandidateRunConfig(model="model-two", agent_mode="multi"),
        )
    )

    assert len(calls) == 2
    assert {(call[2], call[3]) for call in calls} == {
        ("model-one", "single"),
        ("model-two", "multi"),
    }
    assert task.candidate_a.output == "single / model-one"
    assert task.candidate_b.output == "multi / model-two"
    assert task.candidate_a.metadata["trace_id"].startswith("trace-")
    assert task.candidate_b.metadata["trace_id"].startswith("trace-")
    assert task.provenance["source"] == "project_agent_ab_run"
    assert created == [task]
    assert all(callable(policy) for policy in approval_policies)
    assert all(policy("query_metrics", {}) is False for policy in approval_policies)
    assert all(
        policy("run_shell", {"command": "rm -rf /"}) is True
        for policy in approval_policies
    )


async def test_sbs_run_rejects_identical_candidate_configurations(monkeypatch) -> None:
    from fastapi import HTTPException
    from personal_assistant.api import server

    class _Harness:
        async def run_user_turn(self, *_args, **_kwargs):
            raise AssertionError("identical configurations must not consume model tokens")

    monkeypatch.setattr(server, "harness", _Harness())

    with pytest.raises(HTTPException) as exc_info:
        await server.run_sbs_candidates(
            SBSRunRequest(
                prompt="同一提示词",
                candidate_a=SBSCandidateRunConfig(model="same-model", agent_mode="single"),
                candidate_b=SBSCandidateRunConfig(model="same-model", agent_mode="single"),
            )
        )

    assert exc_info.value.status_code == 422
