import json
from collections.abc import AsyncGenerator, Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from personal_assistant.agent.state import AgentState
from personal_assistant.api.schemas import ChatResponse, LLMConfig, ToolCallApproval
from personal_assistant.config import Settings
from personal_assistant.memory.postgres import PostgresMemory
from personal_assistant.skills import SkillRegistry


class AgentHarness:
    def __init__(self, settings: Settings, registry: SkillRegistry, memory: PostgresMemory):
        self.settings = settings
        self.registry = registry
        self.memory = memory
        self.decisions: dict[str, bool] = {}

    async def run_user_turn(
        self,
        thread_id: str,
        message: str,
        llm_config: LLMConfig | None = None,
    ) -> ChatResponse:
        app = self._compile(llm_config)
        result = await app.ainvoke(
            {"messages": [HumanMessage(content=message)]},
            config={"configurable": {"thread_id": thread_id}},
        )
        return _to_response(thread_id, result)

    async def resume_after_approval(
        self,
        thread_id: str,
        approval_id: str,
        approved: bool,
        llm_config: LLMConfig | None = None,
    ) -> ChatResponse:
        self.decisions[approval_id] = approved
        app = self._compile(llm_config)
        result = await app.ainvoke(
            {},
            config={"configurable": {"thread_id": thread_id}},
        )
        return _to_response(thread_id, result)

    async def replay(self, thread_id: str) -> list[dict[str, Any]]:
        return await self.memory.replay(thread_id)

    async def delete_thread(self, thread_id: str) -> None:
        await self.memory.delete_thread(thread_id)

    async def run_user_turn_stream(
        self,
        thread_id: str,
        message: str,
        llm_config: LLMConfig | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream the agent response as SSE events."""
        try:
            app = self._compile(llm_config)
            config = {"configurable": {"thread_id": thread_id}}
            async for event in app.astream_events(
                {"messages": [HumanMessage(content=message)]},
                config=config,
                version="v2",
            ):
                kind = event["event"]
                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    if chunk.content:
                        yield _sse_event("token", {"content": chunk.content})

            # After streaming, inspect final state
            state = await app.aget_state(config)
            values = state.values if state.values else {}
            pending = values.get("pending_approvals") or []

            if pending:
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
    ) -> AsyncGenerator[str, None]:
        """Resume after approval with streaming SSE events."""
        self.decisions[approval_id] = approved

        try:
            app = self._compile(llm_config)
            config = {"configurable": {"thread_id": thread_id}}
            async for event in app.astream_events(
                {},
                config=config,
                version="v2",
            ):
                kind = event["event"]
                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    if chunk.content:
                        yield _sse_event("token", {"content": chunk.content})

            state = await app.aget_state(config)
            values = state.values if state.values else {}
            pending = values.get("pending_approvals") or []

            if pending:
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

        return agent_module.compile_agent(
            self.settings,
            self.registry,
            self.memory,
            self.decisions,
            llm_config,
        )


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
