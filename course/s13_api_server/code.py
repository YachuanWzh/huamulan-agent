#!/usr/bin/env python3
"""
s13_api_server.py — FastAPI 服务：将 Agent 从 CLI 工具变成 Web 服务

在 s01 到 s12 中，Agent 一直通过命令行交互。本章用 FastAPI 给它套上
HTTP 层，让 Agent 变成可被前端、移动端、第三方系统调用的服务。

三个核心端点：
  - POST /chat         普通请求-响应（非流式）
  - POST /chat/stream  SSE 流式推送（每个 Graph 事件即一条 SSE 消息）
  - GET  /health       健康检查

SSE 协议的关键：
  - data: {"type": "...", ...}\n\n
  - 每条消息以空行结尾（\n\n），浏览器 EventSource API 据此解析

Usage:
    pip install fastapi uvicorn sse-starlette langgraph langchain-openai python-dotenv
    OPENAI_API_KEY=... python s13_api_server/code.py

    然后访问 http://localhost:8000/docs 查看 Swagger UI
"""
from __future__ import annotations

import json
import os
import time
from typing import Annotated

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel

load_dotenv(override=True)

# ═══════════════════════════════════════════════════════════
# Pydantic models (对应 api/schemas.py 中的 ChatRequest/ChatResponse)
# ═══════════════════════════════════════════════════════════


class ChatRequest(BaseModel):
    thread_id: str
    message: str


class ChatResponse(BaseModel):
    thread_id: str
    status: str  # "completed" | "requires_approval"
    message: str | None = None


# ═══════════════════════════════════════════════════════════
# Agent graph（复刻 s01 的最小循环）
# ═══════════════════════════════════════════════════════════

from typing import TypedDict


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


@tool
def bash(command: str) -> str:
    """Run a shell command in the workspace."""
    import subprocess

    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except Exception as exc:
        return f"Error: {exc}"


TOOLS = [bash]
TOOL_NODE = ToolNode(TOOLS)

LLM = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "deepseek-chat"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0.2,
)
LLM_WITH_TOOLS = LLM.bind_tools(TOOLS)

SYSTEM = (
    f"You are a coding agent at {os.getcwd()}. "
    "Use bash to solve tasks. Be concise."
)


def agent_node(state: AgentState) -> dict:
    messages = [SystemMessage(content=SYSTEM)] + state["messages"]
    response = LLM_WITH_TOOLS.invoke(messages)
    return {"messages": [response]}


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", TOOL_NODE)
graph.set_entry_point("agent")
graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")
app_graph = graph.compile()

# ═══════════════════════════════════════════════════════════
# FastAPI app
# ═══════════════════════════════════════════════════════════

api = FastAPI(title="LangGraph Agent API - s13")


@api.get("/health")
async def health() -> dict[str, str]:
    """健康检查：K8s / Docker 探活用。"""
    return {"status": "ok"}


@api.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """非流式聊天：一次请求，一次完整响应。

    与 s01 的命令行循环等价——app_graph.invoke() 同步执行整个
    agent loop，返回最终的消息列表。
    """
    result = app_graph.invoke(
        {"messages": [HumanMessage(content=request.message)]},
        {"configurable": {"thread_id": request.thread_id}},
    )
    # 提取最后一条消息的文本内容
    last_msg = result["messages"][-1]
    content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
    return ChatResponse(thread_id=request.thread_id, status="completed", message=content)


def _sse_event(event_type: str, data: dict) -> str:
    """构造一条 SSE 消息。

    SSE 协议格式：
      event: <event_type>
      data: <json_payload>

    末尾的空行（\\n\\n）是协议的分隔符。
    """
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@api.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """SSE 流式聊天：每个 Graph 事件实时推送给客户端。

    真实项目 api/server.py 的 chat_stream 调用 harness.run_user_turn_stream()，
    harness 内部遍历 app_graph.astream_events()。

    本章把这两层折叠为一个生成器函数，直接暴露 astream_events 的用法：
    - on_chat_model_stream → 每收到一个 token 就发一个 SSE "token" 事件
    - on_tool_start / on_tool_end → 工具调用的开始和结束
    - 流结束后发一个 "done" 事件标记完成
    """

    async def event_stream():
        config = {"configurable": {"thread_id": request.thread_id}}
        try:
            async for event in app_graph.astream_events(
                {"messages": [HumanMessage(content=request.message)]},
                config=config,
                version="v2",
            ):
                kind = event["event"]

                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    if chunk.content:
                        yield _sse_event("token", {"content": chunk.content})

                elif kind == "on_tool_start":
                    yield _sse_event("tool_call", {
                        "name": event.get("name", "unknown"),
                        "status": "started",
                        "input": event.get("data", {}).get("input"),
                    })

                elif kind == "on_tool_end":
                    yield _sse_event("tool_call", {
                        "name": event.get("name", "unknown"),
                        "status": "completed",
                        "output": str(event.get("data", {}).get("output", ""))[:500],
                    })

            # 流结束
            yield _sse_event("done", {"status": "completed"})

        except Exception as exc:
            yield _sse_event("error", {"message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        },
    )


# ═══════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════

# 前端可以用 EventSource 连接这个端点：
#
#   const es = new EventSource('/chat/stream');
#   es.addEventListener('token', (e) => { ... });
#   es.addEventListener('tool_call', (e) => { ... });
#   es.addEventListener('done', (e) => { es.close(); });

if __name__ == "__main__":
    uvicorn.run("__main__:api", host="0.0.0.0", port=8000, reload=True)
