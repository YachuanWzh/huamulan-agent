import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from langchain_core.runnables import RunnableConfig

from personal_assistant.agent.state import AgentState

logger = logging.getLogger(__name__)


class HookStage(str, Enum):
    ROUTE_SKILLS = "route_skills"
    COMPACT_CONTEXT = "compact_context"
    AGENT = "agent"
    MEMORY_REFLECTION = "memory_reflection"
    APPROVAL = "approval"
    TOOLS = "tools"


@dataclass(frozen=True)
class HookEvent:
    stage: HookStage
    phase: str
    state: AgentState
    config: RunnableConfig | None = None
    result: Any = None
    error: BaseException | None = None


HookCallback = Callable[[HookEvent], None | Awaitable[None]]


class AgentHookManager:
    def __init__(self, hooks: Iterable[HookCallback] | None = None):
        self._hooks = list(hooks or [])
        self.stages = tuple(HookStage)

    async def emit(self, event: HookEvent) -> None:
        for hook in self._hooks:
            try:
                maybe_awaitable = hook(event)
                if inspect.isawaitable(maybe_awaitable):
                    await maybe_awaitable
            except Exception:
                logger.exception("Agent hook failed")


def with_hooks(
    manager: AgentHookManager,
    stage: HookStage,
    node: Callable[..., Any],
) -> Callable[..., Awaitable[Any]]:
    async def wrapped(state: AgentState, config: RunnableConfig | None = None) -> Any:
        await manager.emit(HookEvent(stage=stage, phase="before", state=state, config=config))
        try:
            result = await _call_node(node, state, config)
        except Exception as exc:
            await manager.emit(
                HookEvent(stage=stage, phase="error", state=state, config=config, error=exc)
            )
            raise
        await manager.emit(
            HookEvent(stage=stage, phase="after", state=state, config=config, result=result)
        )
        return result

    wrapped._hook_stage = stage  # type: ignore[attr-defined]
    return wrapped


async def _call_node(
    node: Callable[..., Any],
    state: AgentState,
    config: RunnableConfig | None,
) -> Any:
    if _accepts_config(node):
        result = node(state, config)
    else:
        result = node(state)
    if inspect.isawaitable(result):
        return await result
    return result


def _accepts_config(node: Callable[..., Any]) -> bool:
    signature = inspect.signature(node)
    parameters = signature.parameters.values()
    return any(
        parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        or parameter.name == "config"
        for parameter in parameters
    )
