# s01: Agent Loop（LangGraph 最小循环）

`[ s01 ] s02 > s03 > s04 > s05 > s06 > s07 > s08 > s09 > s10 > s11 > s12 > s13 > s14 > s15 > s16`

> *"One graph & one tool is all you need"* —— 一个 StateGraph + 一个 Bash 工具 = 一个 Agent。
>
> **Harness 层**: 循环 —— 模型与真实世界的第一道连接。

## 问题

语言模型能推理代码，但碰不到真实世界——不能读文件、跑测试、看报错。你需要一个
**循环**：模型输出工具调用 → 执行工具 → 把结果喂回模型 → 模型继续推理或停止。

Claude Code 的原始实现用 `while stop_reason == "tool_use"` 做这个循环。
langgraph-claw 用 **LangGraph 的 StateGraph** 做同样的事——用节点和边描述 agent 的
行为流程，而不是手写 while 循环。

## 解决方案

```
+--------+      +-------------+      +---------+
|  User  | ---> | agent_node  | ---> |  tools  |
| prompt |      | (LLM call)  |      | (bash)  |
+--------+      +------+------+      +----+----+
                       ^                  |
                       |   tool_result    |
                       +------------------+
                  (loop via conditional edge)
```

LangGraph 用一个 `StateGraph` 描述这个流程：

- **节点 (node)**: `agent`（调 LLM）和 `tools`（执行工具）
- **边 (edge)**: 固定边（`tools → agent`）和条件边（`agent → tools` 或 `agent → END`）
- **状态 (state)**: 消息列表，在节点间传递和累加

## 工作原理

### 1. 定义状态

```python
class AgentState(TypedDict):
    messages: list  # 消息累加器
```

### 2. 定义工具

```python
def bash_tool(command: str) -> str:
    """Run a shell command."""
    r = subprocess.run(command, shell=True, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip() or "(no output)"
```

### 3. 构建 LLM 节点

```python
def agent_node(state: AgentState) -> dict:
    messages = [SystemMessage(content=SYSTEM)] + state["messages"]
    response = LLM_WITH_TOOLS.invoke(messages)
    return {"messages": [response]}
```

### 4. 路由逻辑：条件边

```python
def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"   # 模型想调工具 → 去 tools 节点
    return END           # 模型输出文本 → 结束
```

### 5. 组装为完整 Graph

```python
graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", ToolNode([bash_tool]))
graph.set_entry_point("agent")
graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")
app = graph.compile()
```

这就是整个 Agent。不到 80 行。后面 15 章都在这个 StateGraph 上叠加机制——
**Graph 本身始终不变**。

### 对比：while 循环 vs StateGraph

| | while 循环 (Claude Code) | StateGraph (langgraph-claw) |
|---|---|---|
| 控制流 | 手写 `while True` + `if` | 声明式节点 + 条件边 |
| 状态管理 | 手动管理 `messages` 列表 | `AgentState` 自动累加 |
| 可扩展性 | 在循环里加 if-else | 添加节点 + 边即可 |
| Checkpoint | 需自己实现 | LangGraph 内置 |
| 可视化 | 无 | 自动生成图 |
| 流式输出 | 手动处理 | LangGraph stream |

### 为什么 langgraph-claw 选 LangGraph

核心原因：**checkpoint**。LangGraph 内置了每个节点后的状态快照能力。
langgraph-claw 利用这一点实现了：

- 每个工具调用后可恢复（线程中断-恢复）
- 审批流程（在 approval 节点等待用户决策）
- Redis 加速 checkpoint 写入（s11）
- 线程历史回放

如果手写 while 循环，这些都需要自己实现。LangGraph 让 harness 工程师可以
专注于机制而非基础架构。

## 变更内容

| 组件 | 之前 | 之后 |
|------|------|------|
| Agent 循环 | （无） | `StateGraph` + 条件边 |
| 工具 | （无） | `bash`（单一工具） |
| 状态 | （无） | `AgentState` + 消息累加 |
| LLM 集成 | （无） | `ChatOpenAI` + `bind_tools` |

## 试一试

```sh
cd course
python s01_agent_loop/code.py
```

试试这些 prompt：

1. `Create a file called hello.py that prints "Hello, World!"`
2. `List all Python files in this directory`
3. `What is the current git branch?`
4. `Create a directory called test_output and write 3 files in it`

## 下一步

[s02: Tool System](../s02_tool_system/) —— 从单个 bash 工具扩展到完整的工具体系：
read_file, write_file, list_directory, search_files。用 ToolNode 统一管理，
理解工具定义的结构和最佳实践。
