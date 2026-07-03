# s06: Approval Gate（审批管线）

`[ s01 ] s02 > s03 > s04 > s05 > s06 > s07 > s08 > s09 > s10 > s11 > s12 > s13 > s14 > s15 > s16`

> *"Trust, but verify"* —— 每次工具执行前，先问："这次操作安全吗？"
>
> **Harness 层**: 审批 —— 给 Agent 戴上缰绳。

## 问题

Agent 有了工具，能读文件、写文件、执行命令。但问题是：**谁来批准这些操作？**

假设 Agent 决定执行 `rm -rf /tmp/*` 或 `write_file(path="config.yaml", ...)`。
如果没有任何审批机制，Agent 可以在没有任何人类监督的情况下修改或删除文件。
一个真正的 Personal Assistant 需要边界——读操作可以自动放行，但写/删除操作必须经过用户同意。

这就是 **Approval Gate** 要解决的问题：在工具执行之前，插入一个审批检查点。
如果操作需要批准，graph 暂停，等待用户做出决定。

## 解决方案

在 agent 节点和 tools 节点之间插入一个 **approval 节点**。这个节点检查 pending
tool calls 的审批状态：如果全部已决定 → 路由到 tools（执行批准的）或直接返回（全部拒绝）；
如果有待审批的 → graph 暂停，等待用户输入。

```
+--------+      +-------------+      +-------------+      +---------+
|  User  | ---> | agent_node  | ---> | approval    | ---> |  tools  |
| prompt |      | (LLM call)  |      | (check      |      | (exec)  |
+--------+      +------+------+      | decisions)  |      +----+----+
                       ^             +------+------+           |
                       |                    |                  |
                       |              +-----+-----+           |
                       |              |           |            |
                       |          [pending]   [denied]         |
                       |              |           |            |
                       |          +---+---+   +---+---+       |
                       |          | WAIT  |   | END   |       |
                       |          | (pause)|   |(error)|       |
                       |          +-------+   +-------+       |
                       |                                       |
                       +---------------------------------------+
                              (loop: tool_result → agent)
```

LangGraph 的 **条件路由** 是实现审批管线的关键机制：

- **条件边** `approval → wait`：当有 pending 审批时，graph 路由到 END（暂停）。
  用户做出决定后，再次 `invoke` 同一个线程，graph 从 approval 节点恢复。
- **条件边** `approval → tools`：所有调用已批准且尚未执行，路由到 tools 节点。
- **条件边** `approval → END`：所有调用已被拒绝（或 agent 没有工具调用）。

## 工作原理

### 1. 审批规则：什么是危险操作？

```python
READ_ONLY_TOOLS = {"read_file", "list_directory", "search_files"}

def requires_approval(tool_call) -> bool:
    """写/删除操作需要审批；读操作自动放行。"""
    return tool_call.get("name") not in READ_ONLY_TOOLS
```

简单的启发式：工具名称不在只读白名单中 → 需要审批。真实项目还支持
`RequiresApproval` 回调定制（`agent/approval.py:10`），允许按参数粒度判断。

### 2. 审批状态：检查 pending 调用

```python
# AgentState 新增两个字段
class AgentState(TypedDict):
    messages: list                    # 消息累加器
    pending_approvals: list[dict]     # 待审批的 tool call 列表
    approval_decisions: dict[str, bool]  # approval_id → True(同意)/False(拒绝)
```

审批节点检查最后一条 AIMessage 的 `tool_calls`。对每个调用：
- 如果 `requires_approval` 返回 True 且该调用尚未有决策 → 加入 `pending_approvals`
- 如果已有决策且为 False（拒绝）→ 生成一条 `ToolMessage(content="Tool call denied")`
- 如果已有决策且为 True（批准）→ 不做处理，调用将路由到 tools 节点

### 3. 决策流：批准 vs 拒绝

```
tool_call 进来
    │
    ├── 不需要审批？──→ 直接放行到 tools
    │
    ├── 需要审批 & 尚未决定？──→ pending_approvals，graph 暂停
    │
    ├── 需要审批 & 已批准？──→ 路由到 tools 执行
    │
    └── 需要审批 & 已拒绝？──→ 生成 ToolMessage("denied")，返回给 agent
```

### 4. 暂停与恢复：LangGraph 如何实现"等待"

LangGraph 本身不提供 `interrupt()` API（这是 LangGraph Platform 的特性）。
在 langgraph-claw 中，"暂停"是通过 **条件路由到 END** 实现的：

```python
def approval_route(state: AgentState) -> str:
    if state.get("pending_approvals"):
        return "wait"   # 路由到 END → graph 执行结束
    # ... 否则路由到 tools 或 agent
```

当 graph 结束时 `pending_approvals` 非空，外部调用者（API 层）检测到这个状态，
向前端发送 `requires_approval` 事件。用户点击"批准"或"拒绝"后，API 调用
`app.ainvoke({"approval_turn_count": 1}, config)` 恢复执行。
approval 节点重新运行，这次 `approval_decisions` 中已有决策，不再暂停。

### 5. 批量审批

多个 tool call 可以在同一个 AIMessage 中出现。审批节点一次性检查所有调用，
把所有待审批的打包到 `pending_approvals` 列表中。用户可以一次性批准/拒绝多个调用。

```python
# 真实项目中，前端发送批量决策
POST /api/approve
{
    "decisions": [
        {"approval_id": "abc", "approved": true},
        {"approval_id": "def", "approved": false}
    ]
}
```

在本章 code.py 中，我们用 stdin 交互模拟这个过程：列出所有待审批调用，
用户输入 `y` 批准全部，`n` 拒绝全部（简化版批量决策）。

## Graph 更新

```
                 ┌──────────────┐
                 │   __start__  │
                 └──────┬───────┘
                        │
                 ┌──────▼───────┐
                 │    agent     │
                 │  (LLM call)  │
                 └──────┬───────┘
                        │
                 ┌──────▼───────┐     pending
                 │  approval    │─────► END (wait)
                 │  (inspect)   │
                 └──┬───────┬───┘
                    │       │
            no tools│       │ approved
                    │       │
                 ┌──▼──┐ ┌──▼──────┐
                 │ END │ │  tools   │
                 └─────┘ │ (exec)   │
                         └────┬─────┘
                              │
                         ┌────▼─────┐
                         │  agent    │
                         │ (continue)│
                         └──────────┘
```

与 s01 的核心区别：agent → tools 不再是一条直连边，而是插入了一个有条件的
approval 节点。工具调用不再是"默认执行"，而是"按需审批后执行"。

## 变更内容

| 组件 | 之前 (s01) | 之后 (s06) |
|------|-----------|-----------|
| AgentState | `messages` | `messages` + `pending_approvals` + `approval_decisions` |
| Graph 节点 | `agent`, `tools` | `agent`, `approval`, `tools` |
| 边 | `agent → tools` (固定) | `agent → approval` (固定), `approval → tools/END` (条件) |
| 工具执行 | 全部自动执行 | 读操作自动，写操作需审批 |
| 工具集 | `bash`（单工具） | `read_file`, `write_file`（读/写分离） |

## 参考源码

- **`backend/src/personal_assistant/agent/approval.py`** — `ApprovalGate` 类：
  `inspect()` 方法检查 AIMessage 的 tool_calls，确定哪些需要审批，哪些已被决定。
  `requires_tool_approval()` 函数定义只读白名单。

- **`backend/src/personal_assistant/agent/harness.py:1063-1119`** — 路由函数：
  `_entry_route()` 判断是否应该直接跳到 approval（恢复时跳过 route_skills）；
  `_approval_route()` 判断 approval 之后的去向（wait / tools / agent / end）。

- **`backend/src/personal_assistant/agent/state.py:8-13`** — AgentState 定义：
  `pending_approvals` 和 `approval_turn_count` 字段。

> **注意**：真实项目中，审批决策通过 REST API 传入（前端按钮触发），而非 stdin。
> 审批状态存储在 `AgentHarness.decisions` 字典中，线程安全地在多次 `invoke` 之间
> 传递。本章 code.py 用 stdin 交互模拟这个流程，以便独立运行。

## 试一试

```sh
cd course
python s06_approval_gate/code.py
```

试试这些 prompt：

1. `Read the file named hello.py` — 读操作，自动放行，无需审批
2. `Create a file called test.txt with content "hello world"` — 写操作，触发审批
3. `Read test.txt and append "more content"` — 读+写，写部分触发审批

观察 approval 节点的行为：当 Agent 要写文件时，终端会提示你批准或拒绝。

## 下一步

[s07: Middleware & Guards](../s07_middleware/) —— 在审批管线之上叠加中间件层：
速率限制、调用次数限制、循环检测、安全守卫。理解中间件链的"短路"模式。
