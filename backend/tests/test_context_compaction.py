import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from personal_assistant.memory.compaction import (
    COMPACTED_PREFIX,
    TOOL_RESULT_REFERENCE_TEMPLATE,
    ContextCompactor,
)


def _human(index: int) -> HumanMessage:
    return HumanMessage(content=f"user {index}")


def test_compactor_keeps_first_user_first_agent_last_agent_and_summary(tmp_path: Path) -> None:
    messages = [
        HumanMessage(content="first user"),
        AIMessage(content="first agent"),
        *[_human(index) for index in range(21)],
        AIMessage(content="last agent"),
    ]
    compactor = ContextCompactor(transcript_dir=tmp_path)

    compacted = compactor.compact(messages)

    assert [message.content for message in compacted] == [
        "first user",
        "first agent",
        compacted[2].content,
        "last agent",
    ]
    assert compacted[2].content.startswith(COMPACTED_PREFIX)


def test_compactor_does_not_trigger_on_twenty_or_fewer_user_turns(tmp_path: Path) -> None:
    messages = []
    for index in range(20):
        messages.append(HumanMessage(content=f"user {index}"))
        messages.append(AIMessage(content=f"assistant {index}"))
    compactor = ContextCompactor(transcript_dir=tmp_path)

    compacted = compactor.compact(messages)

    assert compacted == messages


def test_compactor_counts_approval_clicks_as_conversation_turns(tmp_path: Path) -> None:
    messages = [HumanMessage(content=f"user {index}") for index in range(20)]
    compactor = ContextCompactor(transcript_dir=tmp_path)

    compacted = compactor.compact(messages, additional_turns=1)

    assert compacted != messages


def test_compactor_triggers_when_context_exceeds_ninety_percent_threshold(
    tmp_path: Path,
) -> None:
    messages = [HumanMessage(content="large context")]
    compactor = ContextCompactor(
        transcript_dir=tmp_path,
        token_threshold=1_000_000,
        estimate_tokens=lambda _items: 900_001,
    )

    compacted = compactor.compact(messages)

    assert compacted != messages


def test_compactor_summarizes_tool_results_by_tool_result_id(tmp_path: Path) -> None:
    messages = [
        HumanMessage(content="start"),
        AIMessage(content="first agent"),
        ToolMessage(content="large old result", tool_call_id="tool-result-1"),
        *[_human(index) for index in range(21)],
        AIMessage(content="last agent"),
    ]
    compactor = ContextCompactor(transcript_dir=tmp_path)

    compacted = compactor.compact(messages)

    assert TOOL_RESULT_REFERENCE_TEMPLATE.format(tool_result_id="tool-result-1") in compacted[2].content


@pytest.mark.asyncio
async def test_compactor_writes_transcript_and_replaces_middle_messages_with_summary(
    tmp_path: Path,
) -> None:
    messages = [
        HumanMessage(content="first user"),
        AIMessage(content="first agent"),
        *[_human(index) for index in range(30)],
        AIMessage(content="last agent"),
    ]

    async def summarize(_messages):
        return (
            "==当前目标==\n实现压缩\n"
            "==重要发现 / 决策==\n保留边界消息\n"
            "==已读 / 已改的文件==\n无\n"
            "==剩余工作==\n验证\n"
            "==用户约束==\n需要 JSONL transcript"
        )

    compactor = ContextCompactor(
        transcript_dir=tmp_path,
        token_threshold=20,
        estimate_tokens=lambda items: sum(len(str(item.content).split()) for item in items),
        summarize=summarize,
    )

    compacted = await compactor.acompact(messages, thread_id="thread-1")

    assert len(compacted) == 4
    assert compacted[0].content == "first user"
    assert compacted[1].content == "first agent"
    assert compacted[2].content.startswith("[Compacted]\n")
    assert "==当前目标==" in compacted[2].content
    assert compacted[3].content == "last agent"
    transcript = next(tmp_path.glob("thread-1-*.jsonl"))
    rows = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["type"] == "human"
    assert rows[-1]["content"] == "last agent"


@pytest.mark.asyncio
async def test_compactor_stops_summary_retry_after_three_failures(tmp_path: Path) -> None:
    attempts = 0

    async def summarize(_messages):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("llm unavailable")

    compactor = ContextCompactor(
        transcript_dir=tmp_path,
        token_threshold=1,
        estimate_tokens=lambda _items: 100,
        summarize=summarize,
    )

    compacted = await compactor.acompact([_human(index) for index in range(30)])

    assert attempts == 3
    assert compacted[1].content.startswith("[Compacted]\n")
    assert "Summary unavailable after 3 attempts." in compacted[1].content
