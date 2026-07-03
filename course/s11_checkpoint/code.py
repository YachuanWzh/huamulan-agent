#!/usr/bin/env python3
"""
s11_checkpoint.py -- LangGraph Checkpoint: 状态持久化与回放

演示 LangGraph 内置的 checkpoint 机制：
- 每个 superstep 后自动快照状态
- 通过 thread_id 区分不同会话
- 列出历史 checkpoint、回放到任意历史点
- 概念讲解 Redis-first 写入加速模式

Usage:
    pip install langgraph langchain-core langchain-openai python-dotenv
    OPENAI_API_KEY=... python s11_checkpoint/code.py
"""
import os
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

load_dotenv(override=True)


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    turn: int


LLM = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "deepseek-chat"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0.2,
)

SYSTEM = "You are a helpful assistant. Answer concisely."


def agent_node(state: AgentState) -> dict:
    messages = [SystemMessage(content=SYSTEM)] + state["messages"]
    response = LLM.invoke(messages)
    return {"messages": [response], "turn": state.get("turn", 0) + 1}


def should_continue(state: AgentState) -> str:
    return "agent" if state.get("turn", 0) < 2 else END


# ── Build graph with MemorySaver checkpointer ─────────────
# MemorySaver 适合开发/测试。langgraph-claw 生产环境使用
# RedisFirstCheckpointSaver（见文件末尾概念讲解）。
checkpointer = MemorySaver()

graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.set_entry_point("agent")
graph.add_conditional_edges("agent", should_continue, {"agent": "agent", END: END})
app = graph.compile(checkpointer=checkpointer)


# ── Demo ──────────────────────────────────────────────────
def demo():
    print("=" * 55)
    print("s11: Checkpoint — 状态持久化与回放")
    print("=" * 55)

    # 1. thread_id 决定"哪段对话"
    #    config["configurable"]["thread_id"] 是 checkpoint 体系的核心:
    #    同一 thread_id → 自动从最新快照恢复，消息历史自动拼接。
    config = {"configurable": {"thread_id": "session-001"}}

    print("\n[1] 首次调用 (thread_id=session-001)")
    result = app.invoke(
        {"messages": [HumanMessage(content="What is 2+2?")], "turn": 0}, config
    )
    print(f"    turn={result['turn']}  msgs={len(result['messages'])}")

    # 2. 同一 thread_id 继续 → 自动从上一个快照恢复
    print("\n[2] 同一 thread 追加消息 (自动恢复)")
    result2 = app.invoke(
        {"messages": [HumanMessage(content="Multiply by 3")]}, config
    )
    print(f"    turn={result2['turn']}  msgs={len(result2['messages'])}")

    # 3. 列出全部 checkpoint
    print("\n[3] 历史快照列表 (get_state_history):")
    history = list(app.get_state_history(config))
    print(f"    共 {len(history)} 个:")
    for i, sn in enumerate(history):
        cid = sn.config["configurable"].get("checkpoint_id", "?")[:8]
        t = sn.values.get("turn", "?") if sn.values else "?"
        m = len(sn.values.get("messages", []) if sn.values else [])
        print(f"    [{i}] {cid}...  turn={t}  msgs={m}")

    # 4. 回放到第一个 checkpoint
    if history:
        first_cid = history[-1].config["configurable"]["checkpoint_id"]
        replay = app.get_state(
            {"configurable": {"thread_id": "session-001", "checkpoint_id": first_cid}}
        )
        if replay and replay.values:
            print(f"\n[4] 回放到第一个 checkpoint: turn={replay.values.get('turn')}")

    # 5. 不同 thread_id 完全隔离
    config_b = {"configurable": {"thread_id": "session-002"}}
    result_b = app.invoke(
        {"messages": [HumanMessage(content="Capital of France?")], "turn": 0}, config_b
    )
    print(f"\n[5] 不同 thread 隔离: session-001 有 {len(result2['messages'])} 条消息, "
          f"session-002 有 {len(result_b['messages'])} 条 ✓")


# ── Interactive ────────────────────────────────────────────
if __name__ == "__main__":
    demo()

    print("\n" + "=" * 55)
    print("交互模式 (输入 h 查看历史, q 退出)")
    print("=" * 55)

    config = {"configurable": {"thread_id": "interactive"}}
    messages = []
    while True:
        try:
            q = input("\n\033[36ms11 >> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q.lower() in ("q", "exit", ""):
            break
        if q.lower() == "h":
            for i, sn in enumerate(app.get_state_history(config)):
                cid = sn.config["configurable"].get("checkpoint_id", "?")[:8]
                t = sn.values.get("turn", "?") if sn.values else "?"
                m = len(sn.values.get("messages", []) if sn.values else [])
                print(f"  [{i}] {cid}...  turn={t}  msgs={m}")
            continue

        messages.append(HumanMessage(content=q))
        result = app.invoke({"messages": messages, "turn": 0}, config)
        messages = result["messages"]
        last = messages[-1]
        if hasattr(last, "content") and last.content:
            print(f"\n{last.content}")


# ── Redis-first 模式 (概念讲解) ────────────────────────────
#
# langgraph-claw 生产环境使用 RedisFirstCheckpointSaver:
#
#   [Agent 执行完成]
#        │
#        ▼
#   ┌──────────┐  同步写入 (~1ms)              ┌────────────┐
#   │  Redis   │ ◄─── 关键路径（阻塞 agent）     │ PostgreSQL │
#   └────┬─────┘                                └──────┬─────┘
#        │  异步归档 (background task)                 │
#        └────────────────────────────────────────────┘
#
# 为什么需要 Redis-first？
#   - checkpoint 写入在 agent 关键路径上
#   - PG 写入 ~10-50ms，Redis ~1ms
#   - 慢写入 = 慢 agent，直接影响用户体验
#   - Redis TTL 7 天自动过期，PG 永久存储用于审计
#
# 数据流:
#   aput() → 同步写 Redis → 返回 → agent 继续
#          → asyncio.create_task() 异步归档到 PG
#   aget_tuple() → 优先读 Redis → miss 则回退 PG
#   drain() → shutdown 时等待未完成的归档任务
#
# Checkpointer 选择:
#   MemorySaver     - 开发/测试（本文件使用）
#   SqliteSaver     - 本地单机轻量持久化
#   PostgresSaver   - 生产标准方案
#   RedisSaver      - 写入加速层（配合 PG 使用）
#
# 参考: backend/src/personal_assistant/checkpoint/redis_first.py
# 入口: backend/src/personal_assistant/memory/postgres.py
#       (PostgresMemory._build_checkpointer)
