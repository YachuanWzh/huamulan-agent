# s09: Context Compaction（上下文压缩）

`[ s01 > s02 > s03 > s04 > s05 > s06 > s07 > s08 > s09 ] s10 > s11 > s12 > s13 > s14 > s15 > s16`

> *"上下文总会满，要有办法腾地方"* —— LLM 的上下文窗口是有限的；长时间对话会
> 推高延迟、增加成本、降低推理质量。本节实现自动压缩：当对话过长时，归档全文、
> 生成摘要、替换中间消息。
>
> **Harness 层**: 记忆持久化 —— 让 Agent 的记忆不被上下文窗口限制。

## 问题

LLM 的上下文窗口有硬上限。一个编码 Agent 在一次对话中可能产生数百条消息——
读文件、改文件、跑测试、看输出——每条消息都在消耗 token。问题：

- **性能下降**：上下文越大，LLM 推理越慢，注意力稀释导致质量下降
- **成本上升**：每次 LLM 调用都要处理全部历史，token 消耗线性增长
- **硬截断风险**：超出窗口上限时，最早的消息被截断，Agent 丢失关键上下文
- **无限循环放大**：Agent 陷入工具循环时，消息爆炸性增长

Claude Code 的原始实现使用 `compact_and_continue` 函数处理这个问题。
langgraph-claw 将它抽象为 `ContextCompactor` 类 + `compact_context` 图节点。

## 解决方案

```
                          compaction check point
                                 |
+--------+      +-------------+  |  +------------------+      +---------+
|  User  | ---> | agent_node  | ---> | compact_context  | ---> |  tools  |
| prompt |      | (LLM call)  |     | (threshold check)|      | (bash)  |
+--------+      +------+------+     +---------+--------+      +----+----+
                      ^                         |                   |
                      |          (no compact)   |                   |
                      +-------------------------+-------------------+
                           (loop back via conditional edges)
```

当 `compact_context` 节点检测到消息数或 token 数超过阈值时：

1. **归档**：完整对话写入 `.transcripts/thread_xxx.jsonl`（一行一条消息）
2. **摘要**：用 LLM 对全部历史生成结构化摘要（当前目标、关键决策、已读文件、剩余工作）
3. **替换**：保留开头和结尾各约 2 条消息，中间替换为 `[Compacted]` + 摘要

Agent 看到摘要即可继续工作；如果摘要不够，可以从 transcript 文件中回溯。

## 工作原理

### 1. 触发条件：双重阈值

```python
def should_compact(self, messages: list) -> bool:
    return (
        len(messages) >= self.message_threshold    # 默认 20 条
        or estimate_tokens(messages) >= self.token_threshold  # 默认 100K tokens
    )
```

两条条件，满足任一即触发。消息数简单直观（1 turn = 2 条消息：human + ai）；
token 数更精确但需要估算。langgraph-claw 生产环境用 `tiktoken`，教学版用
`chars // 4` 近似（英语 1 token ≈ 4 字符，中文需要系数 2）。

### 2. 压缩策略：保留头尾，替换中间

```
压缩前:  [H1, A1, H2, A2, ... H18, A18, H19, A19]   (38 messages)
                  ↓
压缩后:  [H1, A1,  [Compacted] summary,  A19]          (4 messages)
           ↑  ↑         ↑                 ↑
       system   setup   LLM 摘要      最近一次回复
```

具体规则：
- **保留 H1**（第一个用户消息）：包含初始任务和约束
- **保留 A1**（第一个 AI 响应）：包含任务理解和方法选择
- **保留 A_last**（最后一个 AI 响应）：最近的上下文，Agent 继续推理的起点
- **中间全部替换**为一个 `[Compacted] HumanMessage`，内容是 LLM 生成的摘要

### 3. 摘要生成：LLM 自总结

```python
def _generate_summary(self, messages: list[AnyMessage]) -> str:
    prompt = (
        "You are summarizing a long conversation for context compaction.\n"
        "Write a concise summary covering:\n"
        "1. Current goal\n"
        "2. Key decisions made\n"
        "3. Files read/modified\n"
        "4. Remaining work\n"
        "5. User constraints\n"
    )
    # ... feed conversation text to LLM ...
```

与 learn-claude-code 的区别：learn-claude-code 用一个独立的"压缩层"来处理
压缩逻辑，langgraph-claw 直接用同一个 LLM 做摘要——减少了一次模型切换，
简化了依赖。生产环境中可以通过 `summarize` 参数注入更便宜的模型。

### 4. 归档：可追溯的完整 transcript

```python
def _write_transcript(self, messages: list, *, thread_id: str) -> Path:
    path = self.transcript_dir / f"thread_{safe_id}_{timestamp}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for m in messages:
            record = {"type": m.__class__.__name__, "content": getattr(m, "content", "")}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path
```

JSONL 格式，每行一条消息。设计考量：
- `ensure_ascii=False` 保留中文原文
- 文件名包含 thread_id + 时间戳，防止覆盖
- 归档目录 `.transcripts/` 独立于代码，可随时清理

### 5. 在 Graph 中的位置

```
[entry] -> [agent] -> {tool_calls?} -> [tools] -> [compact_context] -> [agent] -> ...
                          |                                    |
                      {no calls} -> END              (compaction check
                                                       before next LLM call)
```

`compact_context` 放在 tools 之后、agent 之前。为什么选这个位置？

- **不放在 agent 之前**：首次进入时消息很少，无需检查
- **不放在 tools 之前**：tool 调用本身不增加很多消息
- **放在 tools 之后**：每次完整迭代（agent + tool）后检查一次，时机最合理

### 6. Four-Layer Compaction Strategy（对比 learn-claude-code）

learn-claude-code 的上下文压缩采用四层策略：

| 层级 | 策略 | learn-claude-code | langgraph-claw |
|------|------|-------------------|----------------|
| L1  | 消息截断 | 保留最近 N 条 | 保留首尾，替换中间 |
| L2  | Token 截断 | 按 token 预算截断 | 按 token 阈值触发 |
| L3  | LLM 摘要 | 独立压缩层（Claude 调用） | 同一 LLM 自总结 |
| L4  | 归档 | 无 | JSONL 文件归档 |

langgraph-claw 的简化：
- **更少的外部依赖**：不引入额外的压缩模型
- **更强的可追溯性**：归档文件让调试和审计成为可能
- **Graph 原生集成**：压缩是图的一个节点，而非外部函数调用
- **Tool result 引用保留**：工具结果被替换为引用 ID（`[tool result can find by tool_result_id: xxx]`），Agent 需要时可重新获取

## 变更内容

| 组件 | 之前 | 之后 |
|------|------|------|
| Agent Graph | agent + tools 两个节点 | + compact_context 节点 |
| 消息管理 | 无限累积 | 阈值触发压缩 |
| 上下文质量 | 随对话增长而下降 | 保持稳定 |
| 调试能力 | 只能看当前消息 | 可从 transcript 回溯 |
| 消息数量 | 无上限 | 压缩后 ~4 条消息 |

## 试一试

```sh
cd course
python s09_context_compaction/code.py
```

试试这些场景：

1. **短对话不触发**：发 3-5 条简单 query，观察消息数 — 始终低于阈值，不压缩
2. **手动触发压缩**：修改 `message_threshold=6`（而非 20），然后快速发几条消息 — 看到 `[Compacted]` 出现
3. **检查归档**：查看 `.transcripts/` 目录下的 JSONL 文件 — 每行一个完整消息记录
4. **验证继续性**：压缩触发后发一个关于之前工作的 query，Agent 能否从摘要中推理

## 源码参考

- **核心实现**: `backend/src/personal_assistant/memory/compaction.py`
  - `ContextCompactor` 类：阈值检查 + 压缩逻辑
  - `_semantic_compact()`：语义压缩（保留首尾 + 摘要）
  - `_compact_tool_result()`：工具结果替换为引用
  - `_estimate_tokens()`：token 估算
  - `write_transcript()`：JSONL 归档

## 下一步

[s10: Long-Term Memory](../s10_long_term_memory/) —— 上下文压缩解决的是"当前对话太长"的问题。
长期记忆解决的是"跨对话保留信息"的问题——哪些文件被改过、用户的偏好、项目的约定，
这些东西不能每次对话都重新问一遍。
