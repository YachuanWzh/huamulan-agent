# s10: Long-Term Memory（长期记忆）

`[ s01 > s02 > s03 > s04 > s05 > s06 > s07 > s08 > s09 > s10 ] s11 > s12 > s13 > s14 > s15 > s16`

> *"记住该记的，忘掉该忘的"* —— 跨 session 的持久化记忆：选择、提取、整合。
>
> **Harness 层**: 记忆 —— 让模型拥有跨对话的"自我意识"。

## 问题

Checkpoint（s11）保存的是单个 session 的消息历史——session 结束，状态清空。
但 agent 需要记住**跨 session** 的信息：

- "用户喜欢用中文回复" —— 用户偏好
- "项目采用 Redis-First Checkpoint 方案" —— 架构决策
- "所有 API 用 `/api/v1/` 前缀" —— 项目约定

这些信息不会出现在当前对话中，但必须在每次 session 启动时注入到 system prompt
里。你需要一个**长期记忆系统**——在 session 之间持久化知识。

langgraph-claw 的实现用 `.memory/` 目录做文件存储，用 LLM reflection 做记忆的
选择与提取。核心理念来自人脑记忆：**不是记住一切，而是选择性地记住重要的**。

## 解决方案

```
每次 agent 响应后：

+------------------+
|  Agent Response  |
+--------+---------+
         |
         v
+--------+---------+
| memory_reflection |  ← 后台 LLM 调用（可选）
| "有什么值得记住？"  |
+--------+---------+
         |
    +----+----+
    |         |
    v         v
  记住      忽略
    |
    v
+--------+---------+
|  .memory/ 目录    |
| ├── USER.md      |  ← 用户维度：偏好、背景、风格
| ├── SYSTEM.md    |  ← 系统维度：约定、决策、知识
| ├── MEMORY.md    |  ← 索引：所有记忆的目录
| └── *.md         |  ← 单条记忆：YAML frontmatter + body
+------------------+

Session 启动时：
+--------+---------+
| read_all()       |  ← 读取全部记忆
+--------+---------+
         |
         v
+--------+---------+
|  System Prompt   |  ← 注入到 prompt 上下文
+------------------+
```

## 工作原理：三个子系统

### 1. Selection —— 选择：什么值得记住

不是每条消息都值得持久化。LLM reflection 是"守门员"：

```
对话内容 → LLM 反思 → {记住, 忽略}

记住的标准：
  ✓ 用户偏好（风格、语言、习惯）
  ✓ 技术决策（选型、架构约定）
  ✓ 项目知识（目录结构、命名规范）
  ✓ 重要上下文（用户背景、技能栈）

忽略的标准：
  ✗ 临时性操作（"帮我改个文件名"）
  ✗ 一次性任务（"今天天气怎么样"）
  ✗ 已在现有记忆中的重复信息
```

在 langgraph-claw 中，reflection 是一个**可选的**后台 LLM 调用——每次
agent 响应后异步触发。它不阻塞用户交互，失败也不影响主流程。

### 2. Extraction —— 提取：如何表达一个事实

选定"值得记住"后，LLM 需要把它提炼为**一条自包含的事实**：

```
原始对话：
"对了，我比较喜欢简洁的代码风格，变量名能短就短，注释用中文写"

↓ Extraction

记忆条目：
name: user-preference-code-style
content: |
  用户偏好简洁代码风格。变量名简短。注释使用中文。
category: user
tags: preference, code-style
```

提取规则：
- **一个事实一个文件** —— 方便独立读取、更新、删除
- **自包含** —— 脱离对话上下文后仍可理解
- **结构化** —— YAML frontmatter 存放元数据（name、category、tags、created）
- **可检索** —— 通过 MEMORY.md 索引快速定位

### 3. Consolidation —— 整合：如何组织到文件系统

提取后的事实写入 `.memory/` 目录，并维护索引：

**.memory/ 目录结构：**

```
.memory/
├── USER.md              # 用户档案（自由格式 markdown）
│   - 偏好：中文回复、简洁风格
│   - 背景：全栈工程师，Python + TypeScript
│   - 习惯：Dark theme、VSCode
│
├── SYSTEM.md             # 系统知识（自由格式 markdown）
│   - 架构：Redis-First Checkpoint
│   - 约定：/api/v1/ 前缀、RFC 7807 错误格式
│   - 技术栈：FastAPI + LangGraph + React
│
├── MEMORY.md             # 记忆索引（自动维护）
│   - [user-preference-code-style](user-preference-code-style.md) - 用户偏好简洁...
│   - [tech-decision-redis-cache](tech-decision-redis-cache.md) - 采用 Redis 做...
│   - [convention-api-prefix](convention-api-prefix.md) - API 统一用 /api/v1/...
│
├── user-preference-code-style.md    # 单条记忆（YAML frontmatter + body）
├── tech-decision-redis-cache.md
└── convention-api-prefix.md
```

**整合策略：**

| 操作 | 行为 |
|------|------|
| 新增记忆 | 写入新 `.md` 文件 + 追加 MEMORY.md 索引行 |
| 更新记忆 | 覆盖同 slug 的文件 + 更新索引中的 summary |
| 删除记忆 | 删除文件 + 移除索引行 |
| 去重 | `_upsert_index` 用文件名匹配，同 slug 自动覆盖旧条目 |

## 与 Checkpoint 的关系

| | Short-Term (Checkpoint) | Long-Term (Memory) |
|---|---|---|
| **粒度** | Session 级 | Cross-session |
| **内容** | 原始消息历史 | 提炼后的事实 |
| **存储** | LangGraph Checkpointer | `.memory/` 文件系统 |
| **生命周期** | Session 结束可清空 | 永久保留 |
| **写入时机** | 每个节点后自动写入 | 可选的后台 reflection |
| **读取时机** | 线程恢复时 | Session 启动时注入 prompt |
| **管理** | 自动 | LLM 驱动的选择 + 人工编辑 |

两者互补：Checkpoint 保证**会话内**的连续性和可恢复性；Long-Term Memory 保证
**会话间**的知识积累。人也是如此——短期记忆帮助完成当前任务，长期记忆塑造
持久的人格和能力。

## 变更内容

| 组件 | 之前 | 之后 |
|------|------|------|
| 跨 session 知识 | 无持久化 | `.memory/` 文件存储 |
| 记忆写入 | 无 | LLM reflection + `save_memory()` |
| 记忆读取 | 无 | `read_all()` → system prompt 注入 |
| 记忆管理 | 无 | 索引维护、去重、删除 |
| 集成 | 独立工具 | 作为 Graph 节点嵌入 agent loop |

## 源码参考

`backend/src/personal_assistant/memory/long_term.py` —— `LongTermMemoryStore`
包含完整的文件读写、索引维护、缓存层（`read_all_cached` 用 Redis 缓存记忆
内容避免重复 I/O）和文件版本哈希机制。

## 试一试

```sh
cd course
python s10_long_term_memory/code.py
```

脚本会：
1. 创建 `.memory/` 目录，初始化 USER.md、SYSTEM.md、MEMORY.md
2. 模拟 4 轮对话，每轮后触发 memory reflection
3. 展示 `.memory/` 目录结构和 MEMORY.md 索引内容
4. 演示 `read_all()` —— 这就是注入到 system prompt 的记忆内容

也可以手动编辑 `.memory/USER.md` 或 `.memory/SYSTEM.md`，添加你的真实偏好——
下次运行任何依赖长期记忆的 agent 时，这些内容会被自动读取。

## 下一步

[s11: Checkpoint](../s11_checkpoint/) —— Redis-First 状态持久化：LangGraph 内置
checkpoint 的读写加速，线程中断-恢复，以及 Redis 与 Memory 两阶段提交。
