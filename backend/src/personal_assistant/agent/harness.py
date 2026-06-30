import json
import logging
import re
import time
from collections import deque
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from personal_assistant.agent.hook import AgentHookManager
from personal_assistant.agent.state import AgentState
from personal_assistant.api.schemas import (
    AuditEventCreate,
    ChatResponse,
    ExecutionLogCreate,
    LLMConfig,
    ToolCallApproval,
)
from personal_assistant.config import Settings
from personal_assistant.memory.postgres import PostgresMemory
from personal_assistant.skills import SkillRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GuardMatch:
    category: str
    severity: str
    reason: str


class SecurityError(RuntimeError):
    pass


class ToolCallMiddleware:
    def pre_tool(self, call: dict[str, Any]) -> ToolMessage | None:
        return None


@dataclass
class RateLimitMiddleware(ToolCallMiddleware):
    max_calls_per_tool: int = 50
    _counts: dict[str, int] = field(default_factory=dict)

    def pre_tool(self, call: dict[str, Any]) -> ToolMessage | None:
        tool_name = _tool_call_name(call)
        self._counts[tool_name] = self._counts.get(tool_name, 0) + 1
        if self._counts[tool_name] <= self.max_calls_per_tool:
            return None
        return _blocked_tool_message(
            call,
            (
                f"RateLimitMiddleware blocked tool '{tool_name}': per-request "
                f"limit is {self.max_calls_per_tool} calls."
            ),
        )


@dataclass
class CallLimitMiddleware(ToolCallMiddleware):
    max_total_calls: int = 20
    block: bool = True
    _count: int = 0

    def pre_tool(self, call: dict[str, Any]) -> ToolMessage | None:
        self._count += 1
        if not self.block or self._count <= self.max_total_calls:
            return None
        return _blocked_tool_message(
            call,
            (
                "CallLimitMiddleware blocked tool call: total tool call limit "
                f"is {self.max_total_calls}."
            ),
        )


@dataclass
class LoopDetectionMiddleware(ToolCallMiddleware):
    window_size: int = 20
    max_repeats: int = 15
    _window: deque[str] = field(default_factory=deque)

    def pre_tool(self, call: dict[str, Any]) -> ToolMessage | None:
        signature = _tool_call_signature(call)
        self._window.append(signature)
        while len(self._window) > self.window_size:
            self._window.popleft()
        if sum(1 for item in self._window if item == signature) < self.max_repeats:
            return None
        return _blocked_tool_message(
            call,
            (
                "LoopDetectionMiddleware blocked repeated tool call: "
                f"'{_tool_call_name(call)}' used the same arguments "
                f"{self.max_repeats} times within the last {self.window_size} tool calls."
            ),
        )


ToolMiddleware = ToolCallMiddleware | RateLimitMiddleware | CallLimitMiddleware | LoopDetectionMiddleware


_PROMPT_GUARD_MESSAGE = "我不能执行这类会绕过系统安全规则的请求。你可以换一种正常业务问题继续。"

_PROMPT_PATTERNS: tuple[tuple[str, str, str, str], ...] = (
    (
        "instruction_override",
        "HIGH",
        "User message attempts to override prior or system instructions.",
        r"(?is)(?:\b(?:forget|ignore|disregard)\b.{0,20}\b(?:previous|above|all|system)\b.{0,20}\b(?:instructions?|rules?|prompts?|constraints?)\b|忽略.{0,10}(?:之前|以上|所有|以前).{0,10}(?:指令|指示|设定|规则|命令|约束|限制|要求))",
    ),
    (
        "system_prompt_leak",
        "HIGH",
        "User message attempts to reveal hidden system or developer instructions.",
        r"(?is)(?:\b(?:output|print|show|reveal|repeat|dump)\b.{0,20}\b(?:your|the)\b.{0,10}\b(?:system|developer)\b.{0,10}\b(?:prompt|instructions?)\b|(?:输出|打印|展示|泄露|复述|逐字).{0,10}(?:系统|开发者|角色).{0,10}(?:提示词|指令|设定|规则))",
    ),
    (
        "role_play_jailbreak",
        "HIGH",
        "User message attempts to activate a jailbreak role or unsafe mode.",
        r"(?is)(?:\byou\s+are\s+now\s+(?:dan|developer\s+mode)\b|\benter\s+developer\s+mode\b|你现在是.{0,10}DAN|进入.{0,10}开发者模式)",
    ),
    (
        "identity_spoof",
        "HIGH",
        "User message claims privileged identity to bypass policy.",
        r"(?is)(?:\bi\s+(?:am|have)\b.{0,12}\b(?:root|superuser)\b.{0,12}\b(?:access|privileges?|permissions?)\b|\bi\s+am\s+(?:the\s+)?(?:admin|administrator)\b.{0,12}\b(?:override|bypass|policy|rules?)\b|我是.{0,10}(?:管理员|root|超级用户).{0,10}(?:绕过|无视|覆盖|解除).{0,10}(?:规则|限制|权限|策略))",
    ),
)

_TOOL_PATTERNS: tuple[tuple[str, str, str, str], ...] = (
    ("disk_format", "CRITICAL", "Tool command may format or overwrite a disk.", r"(?is)(?:\bmkfs(?:\.\w+)?\b|\bdd\b.{0,80}\bof=/dev/)"),
    ("fork_bomb", "CRITICAL", "Tool command matches a shell fork bomb.", r"(?s):\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;?\s*:"),
    ("download_pipe_exec", "CRITICAL", "Tool command downloads content and pipes it to a shell.", r"(?is)\b(?:curl|wget)\b.{0,160}\|.{0,30}\b(?:bash|sh|zsh|powershell|pwsh)\b"),
    ("reverse_shell", "CRITICAL", "Tool command appears to open a reverse shell.", r"(?is)(?:/dev/tcp/|\bnc\b.{0,80}\s-e\b|\bncat\b.{0,80}\s-e\b)"),
    ("privilege_escalation", "CRITICAL", "Tool command attempts privilege escalation.", r"(?is)(?:^|[=;&|]\s*)\b(?:sudo|su|doas)\b"),
    ("delete_or_move_files", "HIGH", "Tool command may delete or move files.", r"(?is)(?:^|[=;&|]\s*)\b(?:rm|del|Remove-Item|mv)\b"),
    ("shutdown_or_process_control", "HIGH", "Tool command may stop the system or kill processes.", r"(?is)(?:\b(?:shutdown|reboot|Stop-Computer|Restart-Computer|killall|pkill|taskkill)\b|(?:^|[=;&|]\s*)\bkill\b)"),
    ("scheduled_task_modification", "HIGH", "Tool command may modify scheduled tasks.", r"(?is)\bcrontab\b.{0,40}(?:-e|-r|>)"),
    ("world_writable_permissions", "HIGH", "Tool command makes files world-writable/executable.", r"(?is)\bchmod\b.{0,40}\b777\b"),
    ("ssh_key_modification", "HIGH", "Tool command may modify SSH keys.", r"(?is)(?:\.ssh[/\\]|authorized_keys|id_rsa|id_ed25519)"),
)

_REASONING_KEYS = ("reasoning_content", "reasoning", "thinking")
_READ_ONLY_TOOL_NAMES = {"read_file"}
_WRITE_FILE_TOOL_NAME = "write_file"
_APPROVAL_OVERRIDABLE_TOOL_GUARD_CATEGORIES = {"delete_or_move_files"}


def _merge_callbacks(
    config: dict[str, Any],
    harness_callbacks: list[Any],
    thread_id: str,
    request_callbacks: list[Any] | None = None,
) -> None:
    """Merge harness-level and per-request callbacks into the LangChain config.

    Only adds the ``callbacks`` key and ``langfuse_session_id`` metadata
    when at least one callback is present — keeping the config clean when
    observability is not configured.
    """
    combined = list(harness_callbacks)
    if request_callbacks:
        combined.extend(request_callbacks)
    if not combined:
        return
    config["callbacks"] = combined
    config.setdefault("metadata", {})
    config["metadata"]["langfuse_session_id"] = thread_id


class AgentHarness:
    def __init__(
        self,
        settings: Settings,
        registry: SkillRegistry,
        memory: PostgresMemory,
        hook_manager: AgentHookManager | None = None,
        callbacks: list[Any] | None = None,
    ):
        self.settings = settings
        self.registry = registry
        self.memory = memory
        self.hook_manager = hook_manager
        self.callbacks = list(callbacks or [])
        self.decisions: dict[str, bool] = {}

    async def run_user_turn(
        self,
        thread_id: str,
        message: str,
        llm_config: LLMConfig | None = None,
        callbacks: list[Any] | None = None,
    ) -> ChatResponse:
        match = scan_prompt_guard(message)
        if match:
            await _record_audit(
                self.memory,
                AuditEventCreate(
                    thread_id=thread_id,
                    source="prompt",
                    category=match.category,
                    severity=match.severity,
                    reason=match.reason,
                    subject=_clip_subject(message),
                    metadata={"prompt_guard_blocked": True},
                ),
            )
            await _record_execution_log(
                self.memory,
                ExecutionLogCreate(
                    thread_id=thread_id,
                    event_type="security",
                    status="blocked",
                    name=match.category,
                    input={"message": _clip_subject(message)},
                    error={"reason": match.reason},
                    metadata={"severity": match.severity, "source": "prompt"},
                ),
            )
            return ChatResponse(
                thread_id=thread_id,
                status="completed",
                message=_PROMPT_GUARD_MESSAGE,
            )
        app = self._compile(llm_config)
        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        _merge_callbacks(config, self.callbacks, thread_id, callbacks)
        started = time.perf_counter()
        await _record_execution_log(
            self.memory,
            ExecutionLogCreate(
                thread_id=thread_id,
                event_type="turn",
                status="started",
                name="user_turn",
                input={"message": _clip_subject(message)},
            ),
        )
        try:
            result = await app.ainvoke(
                {"messages": [HumanMessage(content=message)]},
                config=config,
            )
        except Exception as exc:
            await _record_execution_log(
                self.memory,
                ExecutionLogCreate(
                    thread_id=thread_id,
                    event_type="turn",
                    status="failed",
                    name="user_turn",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    error={"type": exc.__class__.__name__, "message": str(exc)},
                ),
            )
            raise
        await _record_execution_log(
            self.memory,
            ExecutionLogCreate(
                thread_id=thread_id,
                event_type="turn",
                status="completed",
                name="user_turn",
                duration_ms=int((time.perf_counter() - started) * 1000),
            ),
        )
        return _to_response(thread_id, result)

    async def resume_after_approval(
        self,
        thread_id: str,
        approval_id: str,
        approved: bool,
        llm_config: LLMConfig | None = None,
        callbacks: list[Any] | None = None,
    ) -> ChatResponse:
        self.decisions[approval_id] = approved
        await _record_tool_approval_decision(
            getattr(self, "memory", None),
            thread_id,
            approval_id,
            approved,
        )
        app = self._compile(llm_config)
        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        _merge_callbacks(config, self.callbacks, thread_id, callbacks)
        result = await app.ainvoke(
            {"approval_turn_count": 1},
            config=config,
        )
        return _to_response(thread_id, result)

    async def replay(self, thread_id: str) -> list[dict[str, Any]]:
        return await self.memory.replay(thread_id)

    async def list_threads(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self.memory.list_threads(limit=limit)

    async def delete_thread(self, thread_id: str) -> None:
        await self.memory.delete_thread(thread_id)

    async def clear_threads(self) -> list[str]:
        return await self.memory.clear_threads()

    async def list_audit_events(self, thread_id: str | None = None, limit: int = 100):
        return await self.memory.list_audit_events(thread_id=thread_id, limit=limit)

    async def list_tool_errors(self, thread_id: str | None = None, limit: int = 100):
        return await self.memory.list_tool_errors(thread_id=thread_id, limit=limit)

    async def list_execution_logs(self, thread_id: str, limit: int = 500):
        return await self.memory.list_execution_logs(thread_id=thread_id, limit=limit)

    async def execution_log_summary(self, thread_id: str):
        return await self.memory.execution_log_summary(thread_id=thread_id)

    async def run_user_turn_stream(
        self,
        thread_id: str,
        message: str,
        llm_config: LLMConfig | None = None,
        callbacks: list[Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream the agent response as SSE events."""
        try:
            match = scan_prompt_guard(message)
            if match:
                await _record_audit(
                    self.memory,
                    AuditEventCreate(
                        thread_id=thread_id,
                        source="prompt",
                        category=match.category,
                        severity=match.severity,
                        reason=match.reason,
                        subject=_clip_subject(message),
                        metadata={"prompt_guard_blocked": True},
                    ),
                )
                await _record_execution_log(
                    self.memory,
                    ExecutionLogCreate(
                        thread_id=thread_id,
                        event_type="security",
                        status="blocked",
                        name=match.category,
                        input={"message": _clip_subject(message)},
                        error={"reason": match.reason},
                        metadata={"severity": match.severity, "source": "prompt"},
                    ),
                )
                yield _sse_event("done", {"status": "completed", "message": _PROMPT_GUARD_MESSAGE})
                yield "data: [DONE]\n\n"
                return
            app = self._compile(llm_config)
            config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
            _merge_callbacks(config, self.callbacks, thread_id, callbacks)
            async for event in app.astream_events(
                {"messages": [HumanMessage(content=message)]},
                config=config,
                version="v2",
            ):
                kind = event["event"]
                if kind == "on_chain_start":
                    payload = _compaction_started_payload(event)
                    if payload is not None:
                        yield _sse_event("compacting", payload)
                elif kind == "on_chain_end":
                    payload = _compaction_completed_payload(event)
                    if payload is not None:
                        yield _sse_event("compacting", payload)
                elif kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    reasoning = _extract_reasoning_content(chunk)
                    if reasoning:
                        yield _sse_event("reasoning", {"content": reasoning})
                    if chunk.content:
                        yield _sse_event("token", {"content": chunk.content})

            # After streaming, inspect final state
            state = await app.aget_state(config)
            values = state.values if state.values else {}
            pending = values.get("pending_approvals") or []

            if pending:
                await _record_tool_approval_requests(
                    getattr(self, "memory", None),
                    thread_id,
                    pending,
                )
                yield _sse_event("requires_approval", {
                    "approvals": [
                        {"approval_id": a["approval_id"], "tool_call_id": a["tool_call_id"],
                         "name": a["name"], "args": a["args"]}
                        for a in pending
                    ]
                })
            else:
                msg = _extract_last_ai_message(values.get("messages", []))
                yield _sse_event("done", {"status": "completed", "message": msg})
        except Exception as exc:
            yield _sse_event("error", {"message": _stream_error_message(exc)})

        yield "data: [DONE]\n\n"

    async def resume_after_approval_stream(
        self,
        thread_id: str,
        approval_id: str,
        approved: bool,
        llm_config: LLMConfig | None = None,
        callbacks: list[Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Resume after approval with streaming SSE events."""
        self.decisions[approval_id] = approved
        await _record_tool_approval_decision(
            getattr(self, "memory", None),
            thread_id,
            approval_id,
            approved,
        )

        try:
            app = self._compile(llm_config)
            config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
            _merge_callbacks(config, self.callbacks, thread_id, callbacks)
            async for event in app.astream_events(
                {"approval_turn_count": 1},
                config=config,
                version="v2",
            ):
                kind = event["event"]
                if kind == "on_chain_start":
                    payload = _compaction_started_payload(event)
                    if payload is not None:
                        yield _sse_event("compacting", payload)
                elif kind == "on_chain_end":
                    payload = _compaction_completed_payload(event)
                    if payload is not None:
                        yield _sse_event("compacting", payload)
                elif kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    reasoning = _extract_reasoning_content(chunk)
                    if reasoning:
                        yield _sse_event("reasoning", {"content": reasoning})
                    if chunk.content:
                        yield _sse_event("token", {"content": chunk.content})
                elif kind == "on_tool_end":
                    yield _sse_event("tool_result", _tool_result_payload(event))

            state = await app.aget_state(config)
            values = state.values if state.values else {}
            pending = values.get("pending_approvals") or []

            if pending:
                await _record_tool_approval_requests(
                    getattr(self, "memory", None),
                    thread_id,
                    pending,
                )
                yield _sse_event("requires_approval", {
                    "approvals": [
                        {"approval_id": a["approval_id"], "tool_call_id": a["tool_call_id"],
                         "name": a["name"], "args": a["args"]}
                        for a in pending
                    ]
                })
            else:
                msg = _extract_last_ai_message(values.get("messages", []))
                yield _sse_event("done", {"status": "completed", "message": msg})
        except Exception as exc:
            yield _sse_event("error", {"message": _stream_error_message(exc)})

        yield "data: [DONE]\n\n"

    def _compile(self, llm_config: LLMConfig | None):
        from personal_assistant.agent import agent as agent_module

        if self.hook_manager is None:
            return agent_module.compile_agent(
                self.settings,
                self.registry,
                self.memory,
                self.decisions,
                llm_config,
            )
        return agent_module.compile_agent(
            self.settings,
            self.registry,
            self.memory,
            self.decisions,
            llm_config,
            hook_manager=self.hook_manager,
        )


def scan_prompt_guard(message: str) -> GuardMatch | None:
    for category, severity, reason, pattern in _PROMPT_PATTERNS:
        if re.search(pattern, message):
            return GuardMatch(category=category, severity=severity, reason=reason)
    return None


def scan_tool_guard(tool_name: str, args: Any) -> GuardMatch | None:
    if tool_name in _READ_ONLY_TOOL_NAMES:
        return None
    haystack = f"{tool_name}\n{_flatten_tool_args(args)}"
    for category, severity, reason, pattern in _TOOL_PATTERNS:
        if re.search(pattern, haystack):
            return GuardMatch(category=category, severity=severity, reason=reason)
    return None


def guard_tool_call(tool_name: str, args: Any) -> None:
    match = scan_tool_guard(tool_name, args)
    if match:
        raise SecurityError(f"{match.category}: {match.reason}")


def build_default_tool_middlewares() -> list[ToolMiddleware]:
    return [
        RateLimitMiddleware(),
        CallLimitMiddleware(),
        LoopDetectionMiddleware(),
    ]


async def apply_pre_tool_guards(
    calls: Sequence[dict[str, Any]],
    *,
    memory: Any,
    thread_id: str | None,
    middlewares: Sequence[ToolMiddleware],
    approval_decisions: dict[str, bool] | None = None,
) -> tuple[list[dict[str, Any]], list[ToolMessage]]:
    allowed_calls: list[dict[str, Any]] = []
    blocked_messages: list[ToolMessage] = []

    for call in calls:
        security_response = await _pre_tool_security_guard(
            call,
            memory,
            thread_id,
            approval_decisions=approval_decisions,
        )
        if security_response is not None:
            blocked_messages.append(security_response)
            continue

        middleware_response = _run_pre_tool_middlewares(call, middlewares)
        if middleware_response is not None:
            blocked_messages.append(middleware_response)
            continue

        allowed_calls.append(call)

    return allowed_calls, blocked_messages


def _extract_reasoning_content(chunk: Any) -> str:
    for source_name in ("additional_kwargs", "response_metadata"):
        source = getattr(chunk, source_name, None)
        if not isinstance(source, dict):
            continue
        for key in _REASONING_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


async def _record_audit(memory: Any, event: AuditEventCreate) -> None:
    try:
        await memory.record_audit_event(event)
    except Exception:
        logger.exception("Failed to record security audit event")


async def _record_execution_log(memory: Any, log: ExecutionLogCreate) -> None:
    record = getattr(memory, "record_execution_log", None)
    if not callable(record):
        return
    try:
        await record(log)
    except Exception:
        logger.exception("Failed to record execution log")


async def _record_tool_approval_decision(
    memory: Any,
    thread_id: str,
    approval_id: str,
    approved: bool,
) -> None:
    await _record_audit(
        memory,
        AuditEventCreate(
            thread_id=thread_id,
            source="tool",
            category="tool_approval_decision",
            severity="LOW",
            reason="User approved a tool call." if approved else "User denied a tool call.",
            subject=approval_id,
            metadata={"approval_id": approval_id, "approved": approved},
        ),
    )
    await _record_execution_log(
        memory,
        ExecutionLogCreate(
            thread_id=thread_id,
            event_type="approval",
            status="approved" if approved else "denied",
            name="tool_approval_decision",
            input={"approval_id": approval_id},
            metadata={"approval_id": approval_id, "approved": approved},
        ),
    )


async def _record_tool_approval_requests(
    memory: Any,
    thread_id: str,
    approvals: list[dict[str, Any]],
) -> None:
    for approval in approvals:
        await _record_audit(
            memory,
            AuditEventCreate(
                thread_id=thread_id,
                source="tool",
                category="tool_approval_requested",
                severity="LOW",
                reason="Tool call is waiting for user approval.",
                subject=approval.get("name"),
                metadata={
                    "approval_id": approval.get("approval_id"),
                    "tool_call_id": approval.get("tool_call_id"),
                    "tool_name": approval.get("name"),
                    "tool_args": approval.get("args", {}),
                },
            ),
        )
        await _record_execution_log(
            memory,
            ExecutionLogCreate(
                thread_id=thread_id,
                event_type="approval",
                status="started",
                name="tool_approval_requested",
                input={
                    "approval_id": approval.get("approval_id"),
                    "tool_call_id": approval.get("tool_call_id"),
                    "tool_name": approval.get("name"),
                    "tool_args": approval.get("args", {}),
                },
            ),
        )


def _tool_result_payload(event: dict[str, Any]) -> dict[str, str]:
    name = event.get("name")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    output = data.get("output") if isinstance(data, dict) else None
    return {
        "name": name if isinstance(name, str) else "tool",
        "content": _tool_output_text(output),
    }


def _tool_output_text(output: Any) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, ensure_ascii=False)
    except TypeError:
        return str(output)


def _clip_subject(value: str, limit: int = 500) -> str:
    return value if len(value) <= limit else f"{value[:limit]}..."


def _flatten_tool_args(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(f"{key}={_flatten_tool_args(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_tool_args(item) for item in value)
    return str(value)


def _compaction_started_payload(event: dict[str, Any]) -> dict[str, str] | None:
    if event.get("name") != "compact_context":
        return None
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if not _compaction_input_should_emit(data.get("input")):
        return None
    return {"status": "started", "content": "Compacting context"}


def _compaction_completed_payload(event: dict[str, Any]) -> dict[str, str] | None:
    if event.get("name") != "compact_context":
        return None
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    output = data.get("output")
    if not isinstance(output, dict) or "messages" not in output:
        return None
    return {"status": "completed", "content": "Context compacted"}


def _compaction_input_should_emit(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    messages = value.get("messages")
    if not isinstance(messages, list):
        return False
    try:
        additional_turns = int(value.get("approval_turn_count") or 0)
    except (TypeError, ValueError):
        additional_turns = 0
    human_count = 0
    token_estimate = 0
    for message in messages:
        if isinstance(message, dict):
            message_type = message.get("type")
            content = message.get("content", "")
        else:
            message_type = getattr(message, "type", None)
            content = getattr(message, "content", "")
        if message_type == "human":
            human_count += 1
        token_estimate += max(1, len(str(content).split()))
    return human_count + max(0, additional_turns) > 20 or token_estimate > 900_000


async def _pre_tool_security_guard(
    call: dict[str, Any],
    memory: Any,
    thread_id: str | None,
    *,
    approval_decisions: dict[str, bool] | None = None,
) -> ToolMessage | None:
    match = scan_tool_guard(_tool_call_name(call), call.get("args", {}))
    if match is None:
        return None
    if _approved_tool_guard_override(call, match, approval_decisions):
        return None
    await _record_audit(
        memory,
        AuditEventCreate(
            thread_id=thread_id,
            source="tool",
            category=match.category,
            severity=match.severity,
            reason=match.reason,
            subject=_tool_call_name(call),
            metadata={
                "tool_call_id": _tool_call_id(call),
                "tool_name": _tool_call_name(call),
                "tool_args": call.get("args", {}),
                "tool_guard_blocked": True,
            },
        ),
    )
    await _record_execution_log(
        memory,
        ExecutionLogCreate(
            thread_id=thread_id or "",
            event_type="security",
            status="blocked",
            name=match.category,
            input={"tool_name": _tool_call_name(call), "tool_args": call.get("args", {})},
            error={"reason": match.reason},
            metadata={
                "severity": match.severity,
                "source": "tool",
                "tool_call_id": _tool_call_id(call),
            },
        ),
    )
    return _blocked_tool_message(
        call,
        f"SecurityError: {match.category}: {match.reason}",
    )


def _approved_tool_guard_override(
    call: dict[str, Any],
    match: GuardMatch,
    approval_decisions: dict[str, bool] | None,
) -> bool:
    if approval_decisions is None:
        return False
    if approval_decisions.get(_tool_call_id(call)) is not True:
        return False
    if _tool_call_name(call) == _WRITE_FILE_TOOL_NAME:
        return True
    return match.category in _APPROVAL_OVERRIDABLE_TOOL_GUARD_CATEGORIES


def _run_pre_tool_middlewares(
    call: dict[str, Any],
    middlewares: Sequence[ToolMiddleware],
) -> ToolMessage | None:
    for middleware in middlewares:
        response = middleware.pre_tool(call)
        if response is not None:
            return response
    return None


def _blocked_tool_message(call: dict[str, Any], content: str) -> ToolMessage:
    return ToolMessage(tool_call_id=_tool_call_id(call), content=content)


def _tool_call_id(call: dict[str, Any]) -> str:
    return str(call.get("id") or "")


def _tool_call_name(call: dict[str, Any]) -> str:
    return str(call.get("name") or "tool")


def _tool_call_signature(call: dict[str, Any]) -> str:
    return f"{_tool_call_name(call)}:{_stable_json(call.get('args', {}))}"


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError:
        return str(value)


def _sanitize_messages_for_api(
    messages: Sequence[Any],
) -> Sequence[Any]:
    """Strip unanswered tool_calls so the message history is API-compliant.

    OpenAI/DeepSeek require every AIMessage with ``tool_calls`` to be
    followed by ToolMessages for each ``tool_call_id``.  If the graph
    routing has a bug (e.g. resume after approval wait incorrectly routes
    to agent before tools), this function strips the unanswered tool_calls
    so the API call succeeds instead of crashing with a cryptic
    ``BadRequestError``.

    The original state messages are NOT modified — sanitized copies are
    returned for any message that needed stripping.
    """
    sanitized: list[Any] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        if isinstance(m, AIMessage) and m.tool_calls:
            adjacent_tool_messages: list[ToolMessage] = []
            j = i + 1
            while j < len(messages) and isinstance(messages[j], ToolMessage):
                adjacent_tool_messages.append(messages[j])
                j += 1

            adjacent_answered_ids = {tm.tool_call_id for tm in adjacent_tool_messages}
            unanswered = [tc for tc in m.tool_calls if tc["id"] not in adjacent_answered_ids]
            answered = [tc for tc in m.tool_calls if tc["id"] in adjacent_answered_ids]
            if unanswered:
                # Construct a clean AIMessage without the unanswered
                # tool_calls.  Do NOT use model_copy + try/except here —
                # a silent copy failure would re-introduce the original
                # invalid message and reproduce the BadRequestError.
                sanitized_ai = AIMessage(
                    content=m.content,
                    tool_calls=answered if answered else [],
                    id=getattr(m, "id", None),
                    name=getattr(m, "name", None),
                )
                sanitized.append(sanitized_ai)
            else:
                sanitized.append(m)
            if answered:
                answered_ids = {tc["id"] for tc in answered}
                sanitized.extend(
                    tm
                    for tm in adjacent_tool_messages
                    if tm.tool_call_id in answered_ids
                )
            i = j
            continue
        if isinstance(m, ToolMessage):
            i += 1
            continue
        sanitized.append(m)
        i += 1
    return sanitized


def _entry_route(state: AgentState) -> str:
    """Route directly to approval when resuming with unanswered tool calls.

    When the graph paused on ``wait`` (pending approval), the state still
    contains an AIMessage with tool_calls.  Resuming through route_skills →
    agent would send that unanswered AIMessage to the LLM API, which
    requires every assistant message with tool_calls to be followed by
    corresponding tool messages (OpenAI strict requirement).

    By routing directly to approval, we process the user's decision first,
    execute approved tools, and only then call the agent with a valid
    message sequence.
    """
    messages = state.get("messages", [])
    for m in reversed(messages):
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            # Found an AIMessage with tool_calls — check if any are unanswered
            answered_ids = {
                tm.tool_call_id
                for tm in messages
                if isinstance(tm, ToolMessage)
            }
            for tc in m.tool_calls:
                if tc["id"] not in answered_ids:
                    return "approval"
            break  # all tool calls in this message are answered
    return "route_skills"


def _approval_route(state: AgentState) -> str:
    pending = state.get("pending_approvals") or []
    if pending:
        return "wait"

    messages: Sequence[Any] = state.get("messages", [])
    if not messages:
        return "end"

    # Collect answered tool_call_ids
    answered_ids: set[str] = {
        m.tool_call_id
        for m in messages
        if isinstance(m, ToolMessage)
    }

    # If any AIMessage has tool_calls without corresponding ToolMessages,
    # route to tools to execute them (approved and not yet run).
    for m in reversed(messages):
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            if any(tc["id"] not in answered_ids for tc in m.tool_calls):
                return "tools"
            break  # all calls in this message are answered

    last = messages[-1]
    if isinstance(last, ToolMessage):
        return "agent"
    return "end"


def _to_response(thread_id: str, state: AgentState) -> ChatResponse:
    pending = state.get("pending_approvals") or []
    if pending:
        return ChatResponse(
            thread_id=thread_id,
            status="requires_approval",
            approvals=[ToolCallApproval(**item) for item in pending],
        )

    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", "") == "ai" and getattr(message, "content", None):
            return ChatResponse(thread_id=thread_id, status="completed", message=message.content)
    return ChatResponse(thread_id=thread_id, status="completed", message="")


def _extract_last_ai_message(messages: list[Any]) -> str:
    for msg in reversed(messages):
        if getattr(msg, "type", "") == "ai" and getattr(msg, "content", None):
            return msg.content
    return ""


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream_error_message(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__
