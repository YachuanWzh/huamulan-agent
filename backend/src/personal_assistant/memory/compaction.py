import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage


SNIPPED_MESSAGE_TEMPLATE = "[snipped {count} messages]"
EARLIER_TOOL_RESULT_PLACEHOLDER = "[Earlier tool result compacted. Re-run if needed.]"
TOOL_RESULT_REFERENCE_TEMPLATE = "[tool result can find by tool_result_id: {tool_result_id}]"
COMPACTED_PREFIX = "[Compacted]"
SUMMARY_FAILURE_TEXT = "Summary unavailable after 3 attempts."

TokenEstimator = Callable[[Sequence[AnyMessage]], int]
Summarizer = Callable[[Sequence[AnyMessage]], Awaitable[str]]


class ContextCompactor:
    def __init__(
        self,
        *,
        transcript_dir: Path,
        trigger_message_count: int = 20,
        token_threshold: int = 1_000_000,
        token_trigger_ratio: float = 0.9,
        estimate_tokens: TokenEstimator | None = None,
        summarize: Summarizer | None = None,
    ) -> None:
        self.transcript_dir = transcript_dir
        self.trigger_message_count = trigger_message_count
        self.token_threshold = token_threshold
        self.token_trigger_ratio = token_trigger_ratio
        self.estimate_tokens = estimate_tokens or _estimate_tokens
        self.summarize = summarize

    def compact(
        self,
        messages: Sequence[AnyMessage],
        *,
        additional_turns: int = 0,
    ) -> list[AnyMessage]:
        if not self.should_compact(messages, additional_turns=additional_turns):
            return list(messages)
        summary_messages = _summary_messages(messages)
        return _semantic_compact(
            messages,
            _with_tool_result_references(_fallback_summary(summary_messages), summary_messages),
        )

    def should_compact(
        self,
        messages: Sequence[AnyMessage],
        *,
        additional_turns: int = 0,
    ) -> bool:
        return (
            _user_turn_count(messages) + max(0, additional_turns) > self.trigger_message_count
            or self.estimate_tokens(messages) > self.token_threshold * self.token_trigger_ratio
        )

    async def acompact(
        self,
        messages: Sequence[AnyMessage],
        *,
        thread_id: str | None = None,
        additional_turns: int = 0,
        record_span: Callable[..., Awaitable[None]] | None = None,
    ) -> list[AnyMessage]:
        if not self.should_compact(messages, additional_turns=additional_turns):
            return list(messages)

        before_tokens = self.estimate_tokens(messages)
        transcript_path = self.write_transcript(messages, thread_id=thread_id)
        summary_messages = _summary_messages(messages)
        summary = await self._summarize_with_retries(summary_messages)
        result = _semantic_compact(
            messages,
            (
                f"Transcript: {transcript_path}\n\n"
                f"{_with_tool_result_references(summary, summary_messages)}"
            )
        )
        after_tokens = self.estimate_tokens(result)
        saved_tokens = max(0, before_tokens - after_tokens)
        saved_ratio = round(saved_tokens / max(before_tokens, 1), 4)

        if record_span is not None:
            try:
                await record_span(
                    name="compaction",
                    status="completed",
                    duration_ms=0,
                    metadata={
                        "before_tokens": before_tokens,
                        "after_tokens": after_tokens,
                        "saved_tokens": saved_tokens,
                        "saved_ratio": saved_ratio,
                        "trigger_message_count": _user_turn_count(messages) + max(0, additional_turns),
                        "trigger_token_threshold": self.token_threshold,
                        "transcript_path": str(transcript_path),
                    },
                )
            except Exception:
                pass  # Compaction observability failure must not affect the main flow.

        return result

    def write_transcript(
        self,
        messages: Sequence[AnyMessage],
        *,
        thread_id: str | None = None,
    ) -> Path:
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        safe_thread = _safe_name(thread_id or "conversation")
        path = self.transcript_dir / f"{safe_thread}-{timestamp}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for message in messages:
                handle.write(json.dumps(_message_record(message), ensure_ascii=False) + "\n")
        return path

    async def _summarize_with_retries(self, messages: Sequence[AnyMessage]) -> str:
        if self.summarize is None:
            return _fallback_summary(messages)
        for _attempt in range(3):
            try:
                summary = await self.summarize(messages)
            except Exception:
                continue
            if summary.strip():
                return summary.strip()
        return SUMMARY_FAILURE_TEXT


def _semantic_compact(messages: Sequence[AnyMessage], summary: str) -> list[AnyMessage]:
    preserved: list[AnyMessage] = []
    first_user = _first_message(messages, HumanMessage)
    first_agent = _first_message(messages, AIMessage)
    last_agent = _last_message(messages, AIMessage)
    for message in (first_user, first_agent):
        if message is not None and message not in preserved:
            preserved.append(message)
    preserved.append(HumanMessage(content=f"{COMPACTED_PREFIX}\n{summary.strip()}"))
    if last_agent is not None and last_agent not in preserved:
        preserved.append(last_agent)
    return preserved


def _compact_tool_result(message: AnyMessage) -> AnyMessage:
    if isinstance(message, ToolMessage):
        return ToolMessage(
            content=TOOL_RESULT_REFERENCE_TEMPLATE.format(tool_result_id=message.tool_call_id),
            tool_call_id=message.tool_call_id,
            id=getattr(message, "id", None),
            name=getattr(message, "name", None),
        )
    return message


def _summary_messages(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    return [_compact_tool_result(message) for message in messages]


def _with_tool_result_references(summary: str, messages: Sequence[AnyMessage]) -> str:
    references = [
        str(message.content)
        for message in messages
        if isinstance(message, ToolMessage)
        and str(message.content).startswith("[tool result can find by tool_result_id:")
    ]
    if not references:
        return summary
    unique_references = list(dict.fromkeys(references))
    return f"{summary.rstrip()}\n\n" + "\n".join(unique_references)


def _first_message(messages: Sequence[AnyMessage], message_type: type) -> AnyMessage | None:
    for message in messages:
        if isinstance(message, message_type):
            return message
    return None


def _last_message(messages: Sequence[AnyMessage], message_type: type) -> AnyMessage | None:
    for message in reversed(messages):
        if isinstance(message, message_type):
            return message
    return None


def _estimate_tokens(messages: Sequence[AnyMessage]) -> int:
    return sum(max(1, len(str(getattr(message, "content", "")).split())) for message in messages)


def _user_turn_count(messages: Sequence[AnyMessage]) -> int:
    return sum(1 for message in messages if isinstance(message, HumanMessage))


def _message_record(message: AnyMessage) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": getattr(message, "type", message.__class__.__name__),
        "content": getattr(message, "content", ""),
    }
    if isinstance(message, AIMessage) and message.tool_calls:
        record["tool_calls"] = message.tool_calls
    if isinstance(message, ToolMessage):
        record["tool_call_id"] = message.tool_call_id
    return record


def _fallback_summary(messages: Sequence[AnyMessage]) -> str:
    return (
        "==当前目标==\n继续当前对话目标。\n"
        "==重要发现 / 决策==\n详见 transcript。\n"
        "==已读 / 已改的文件==\n详见 transcript。\n"
        "==剩余工作==\n继续处理最新用户请求。\n"
        "==用户约束==\n保留可追溯 transcript。"
    )


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in value).strip("-") or "thread"
