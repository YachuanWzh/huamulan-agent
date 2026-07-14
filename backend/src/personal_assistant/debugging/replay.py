from __future__ import annotations

import json
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class CheckpointRef(BaseModel):
    checkpoint_id: str
    state: dict[str, Any]


class StateChange(BaseModel):
    path: str
    kind: Literal["added", "removed", "changed"]
    before: Any = None
    after: Any = None


class ReplayDiff(BaseModel):
    added: list[StateChange] = Field(default_factory=list)
    removed: list[StateChange] = Field(default_factory=list)
    changed: list[StateChange] = Field(default_factory=list)


class ForkDescriptor(BaseModel):
    source_thread_id: str
    source_checkpoint_id: str
    target_thread_id: str
    execute: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any]


class ReplayDiffRequest(BaseModel):
    before_checkpoint_id: str
    after_checkpoint_id: str


class ReplayForkRequest(BaseModel):
    checkpoint_id: str
    target_thread_id: str | None = None


def diff_checkpoint_states(before: dict[str, Any], after: dict[str, Any]) -> ReplayDiff:
    result = ReplayDiff()
    _walk(_without_checkpoint_identity(before), _without_checkpoint_identity(after), "", result)
    return result


def find_checkpoint(states: list[dict[str, Any]], checkpoint_id: str) -> CheckpointRef:
    for state in states:
        found = state.get("checkpoint_id")
        if not found:
            config = state.get("config") or {}
            found = (config.get("configurable") or {}).get("checkpoint_id")
        if str(found or "") == checkpoint_id:
            return CheckpointRef(checkpoint_id=checkpoint_id, state=state)
    raise LookupError(f"Checkpoint not found: {checkpoint_id}")


def create_fork_descriptor(
    *,
    source_thread_id: str,
    checkpoint: CheckpointRef,
    target_thread_id: str | None,
) -> ForkDescriptor:
    target = target_thread_id or f"fork-{source_thread_id}-{uuid4().hex[:8]}"
    return ForkDescriptor(
        source_thread_id=source_thread_id,
        source_checkpoint_id=checkpoint.checkpoint_id,
        target_thread_id=target,
        provenance={
            "source_thread_id": source_thread_id,
            "source_checkpoint_id": checkpoint.checkpoint_id,
        },
        state=checkpoint.state,
    )


def _walk(before: Any, after: Any, path: str, result: ReplayDiff) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() | after.keys()):
            child_path = f"{path}.{key}" if path else str(key)
            if key not in before:
                result.added.append(_change(child_path, "added", None, after[key]))
            elif key not in after:
                result.removed.append(_change(child_path, "removed", before[key], None))
            else:
                _walk(before[key], after[key], child_path, result)
        return
    if isinstance(before, list) and isinstance(after, list):
        for index in range(max(len(before), len(after))):
            child_path = f"{path}[{index}]"
            if index >= len(before):
                result.added.append(_change(child_path, "added", None, after[index]))
            elif index >= len(after):
                result.removed.append(_change(child_path, "removed", before[index], None))
            else:
                _walk(before[index], after[index], child_path, result)
        return
    if type(before) is not type(after) or before != after:
        result.changed.append(_change(path or "$", "changed", before, after))


def _change(path: str, kind: str, before: Any, after: Any) -> StateChange:
    return StateChange(
        path=path,
        kind=kind,
        before=_compact(before),
        after=_compact(after),
    )


def _compact(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= 2000 else f"{value[:1988]}…[truncated]"
    if value is None or isinstance(value, (int, float, bool)):
        return value
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    return value if len(encoded) <= 2000 else f"{encoded[:1988]}…[truncated]"


def _without_checkpoint_identity(state: dict[str, Any]) -> dict[str, Any]:
    result = dict(state)
    result.pop("checkpoint_id", None)
    config = result.get("config")
    if isinstance(config, dict):
        config = dict(config)
        configurable = config.get("configurable")
        if isinstance(configurable, dict):
            configurable = dict(configurable)
            configurable.pop("checkpoint_id", None)
            config["configurable"] = configurable
        result["config"] = config
    return result
