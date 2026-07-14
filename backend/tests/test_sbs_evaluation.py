import json

import pytest
from pydantic import ValidationError

from personal_assistant.memory.postgres import PostgresMemory
from personal_assistant.skills.evaluation.sbs import (
    SBSCandidate,
    SBSReview,
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
