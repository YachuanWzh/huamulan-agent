# s08: Hook System（生命周期 Hook）

`[ s01 > ... > s08 ] s09 > s10 > ... > s16`

> *"挂在循环上，不写进循环里"* —— 用 Hook 拓展 Agent 行为，不改核心代码。
>
> **Harness 层**: 生命周期事件 —— Agent 的"观察者"体系。

## 问题

前三章我们给 Agent 加了工具 (s02)、技能 (s03-s05)、审批 (s06)、中间件 (s07)。
每次加机制，代码就胖一圈——agent_node 和 tools_node 里塞满了 log、统计、
异常捕获、通知……核心循环越来越难读。

你需要一种方式把**横切关注点**从核心循环中**剥离**出来。不需要改 agent_node
就能在每次工具调用前后记录日志；不需要动 graph 结构就能向外部系统发送追踪事件。

这就是 Hook。

## Hook vs Middleware

s07 的 Middleware 和本章的 Hook 都提供了"嵌入点"，但定位不同：

| | Middleware（中间件） | Hook（钩子） |
|---|---|---|
| **角色** | 守卫 | 观察者 |
| **能力** | 可以**阻断**执行 | **不阻断**执行 |
| **典型场景** | 限流、权限校验、IP 封禁 | 日志、追踪、计时、通知 |
| **失败行为** | 阻止动作继续 | 静默失败，不影响主流程 |
| **类比** | 安检闸机 | 监控摄像头 |

**一句话**：Middleware decides whether to proceed. Hook watches what happens.

## 解决方案

在 agent 循环的关键节点（工具调用、Agent 启动/结束、异常）埋入 Hook 点，
让外部逻辑通过注册回调的方式挂载上来：

```
                  PreToolUse Hook
                       |
  [agent_node] ──→ [with_hooks(tools_node)] ──→ [agent_node]
                       |
                  PostToolUse Hook

  AgentStart ──→ Agent 执行 ──→ AgentEnd
                       |
                  OnError Hook (exception path)
```

核心循环的代码**一行不变**。所有观测、追踪、通知逻辑通过 `AgentHookManager`
注入——"挂在循环上，不写进循环里"。

## 工作原理

### 1. Hook 阶段（HookStage）

```python
class HookStage(str, Enum):
    PRE_TOOL    = "pre_tool"     # 工具调用前
    POST_TOOL   = "post_tool"    # 工具调用后
    AGENT_START = "agent_start"  # Agent 启动
    AGENT_END   = "agent_end"    # Agent 结束
    ON_ERROR    = "on_error"     # 异常发生
```

真实项目 `agent/hook.py` 中的 HookStage 更多：`ROUTE_SKILLS`、`COMPACT_CONTEXT`、
`AGENT`、`MEMORY_REFLECTION`、`APPROVAL`、`TOOLS`。本章展示简化版的核心五个阶段。

### 2. Hook 管理器（AgentHookManager）

```python
class AgentHookManager:
    def __init__(self):
        self._hooks: dict[HookStage, list[Callable]] = defaultdict(list)

    def register(self, stage: HookStage, callback: Callable):
        self._hooks[stage].append(callback)

    def run_hooks(self, stage: HookStage, context: dict):
        for hook in self._hooks.get(stage, []):
            try:
                hook(context)
            except Exception:
                pass  # 静默失败 —— Hook 不阻断执行
```

关键设计：
- **按阶段注册**：每个 Hook 绑定到一个特定的 HookStage
- **静默失败**：Hook 抛异常不影响主流程（这是 Hook 和 Middleware 的本质区别）
- **context 传递**：通过 dict 传递上下文（state、tool_name、tool_input、result、elapsed）

### 3. with_hooks 包装器

```python
def with_hooks(node_fn, hook_manager: AgentHookManager):
    def wrapped(state, *args, **kwargs):
        ctx = {"state": state}
        hook_manager.run_hooks(HookStage.PRE_TOOL, ctx)
        t0 = time.time()
        result = node_fn(state, *args, **kwargs)
        elapsed = time.time() - t0
        ctx.update({"result": result, "elapsed": elapsed})
        hook_manager.run_hooks(HookStage.POST_TOOL, ctx)
        return result
    return wrapped
```

真实项目 `agent/hook.py` 中的 `with_hooks` 更完整：支持 `phase`（before/after/error）、
支持异步、支持 `RunnableConfig` 注入。本章展示同步简化版以便理解核心思想。

### 4. 集成到 Graph

```python
hook_manager = AgentHookManager()
hook_manager.register(HookStage.PRE_TOOL, logging_hook)
hook_manager.register(HookStage.POST_TOOL, timing_hook)

# 核心循环不变，只把 tools_node 包一层
hooked_tools = with_hooks(tools_node, hook_manager)

graph = StateGraph(AgentState)
graph.add_node("tools", hooked_tools)  # 用包装后的节点
# agent_node 不变，graph 结构不变
```

## 真实项目中的应用

`agent/hook.py` 被以下组件消费：

- **Langfuse 追踪 (s15)**：通过 Hook 埋点向 Langfuse 发送 trace event
- **审批管线 (s06)**：在工具执行前触发审批 Hook
- **上下文压缩 (s09)**：在 Agent 结束后触发压缩检查
- **系统通知**：报错时触发钉钉/企业微信告警

## 变更内容

| 组件 | 之前 | 之后 |
|------|------|------|
| 工具节点 | 直接绑定到 graph | 通过 `with_hooks()` 包装 |
| 日志追踪 | 写死在节点函数里 | 注册为 Hook 回调 |
| 计时统计 | 无 | `timing_hook` 自动记录 |
| 异常处理 | 内联 try/except | `ON_ERROR` Hook |
| 扩展性 | 改 graph 代码 | `register()` 即插即用 |

## 试一试

```sh
cd course
python s08_hooks/code.py
```

观察输出，你会看到：

1. **AGENT_START** 钩子：打印 Agent 启动时间
2. **PRE_TOOL** 钩子：打印即将执行的工具名和输入
3. **POST_TOOL** 钩子：打印执行耗时和结果摘要
4. **AGENT_END** 钩子：打印 Agent 结束
5. **ON_ERROR** 钩子：异常时打印错误（如果有）

试试以下 prompt：

1. `List all Python files in course/` —— 观察正常流程的 Hook 输出
2. `Run a command that doesn't exist: nonexistent_cmd_123` —— 观察 ON_ERROR Hook

## 下一步

[s09: Context Compaction](../s09_context_compaction/) —— 当消息历史越来越长，
如何压缩上下文而不丢失关键信息。上下文压缩本身也是通过 Hook 在 Agent 结束后触发的。
