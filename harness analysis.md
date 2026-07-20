# langgraph-claw Harness Engineering 分析报告

> 分析日期：2026-07-20
> 参考文章：Harness Engineering（驾驭工程）核心范式
> 分析范围：项目实际代码与工程实践（不含 superharness 插件描述）

---

## 一、Harness Engineering 核心框架回顾

文章定义 Harness Engineering 的五个核心组件：

| 组件 | 含义 |
|------|------|
| **约束机制** | 限制 Agent 行为边界，防止越权或危险操作 |
| **反馈回路** | 自动化测试、验证和评估体系（CI/CD for AI） |
| **工作流控制** | 任务拆解、状态持久化、多 Agent 编排 |
| **记忆与管理** | 动态上下文管理，避免 Token 溢出或关键信息遗忘 |
| **安全管控** | 输入输出过滤、权限隔离 |

核心公式：**Agent = LLM + Harness**
核心哲学：**人类掌舵，智能体执行（Human Steer, Agent Execute）**

---

## 二、做得好的方面

### 1. 安全管控 — ⭐⭐⭐⭐⭐ 教科书级实现

项目在安全防护上的投入是整篇 Harness Engineering 文章所描述的**最佳实践范本**。

**多层纵深防御（Defense in Depth）：**

```
第 0 层：Prompt Guard（正则 + LLM 双层判定）
  ├── 4 类正则模式（指令覆盖 / 提示泄露 / DAN 越狱 / 身份伪造）
  └── LLM 语义判定（deepseek-v4-flash，置信度阈值 0.8，异常降级）

第 1 层：Tool Guard（10 类危险命令检测）
  ├── CRITICAL 级（直接拦截，不可绕过）：fork_bomb / download_pipe_exec / reverse_shell / privilege_escalation
  └── HIGH 级（可走审批）：delete_or_move_files / shutdown / world_writable_permissions / ssh_key_modification

第 2 层：Middleware 三件套
  ├── RateLimitMiddleware：单工具 ≤ 50 次/轮（防滥用）
  ├── CallLimitMiddleware：总调用 ≤ 20 次/轮（防无限循环）
  └── LoopDetectionMiddleware：滑动窗口检测同参重复 ≤ 15 次（防死循环）

第 3 层：Approval Gate（人工审批门）
  ├── read_file 白名单免审批
  ├── write_file 强制审批（双重保险）
  └── 其他所有工具调用需审批
```

**这完全契合文章的核心公式：Agent = LLM + Harness。** 项目不是依赖 LLM 自己"变乖"，而是在 LLM 外面套了层层防线，每一层独立兜底。

**审计追踪：** 所有安全事件和审批决策都落入 `audit_events` 和 `agent_execution_logs` 表，支持按事件类型筛选、重试链可视化。做到了"事后可追溯"。

**文件写入授权：** `write_file` 无论是否命中 Tool Guard，都需通过 Approval Gate — 这体现了"宁多查一遍，不少防一层"的工程哲学。

### 2. 反馈回路 — ⭐⭐⭐⭐⭐ 完整闭环

文章强调 Harness Engineering 的核心之一是建立"CI/CD for AI"的自动化验证体系。本项目在这方面的投入在同类开源/Lab 项目中属于顶级水平。

**ClawEval 评测体系（三层递进）：**

```
层次 1：Quick 快检
  ├── Single-agent：三层漏斗 Skill 路由评测（正则 → BGE-M3 语义 → LLM）
  ├── Multi-agent：意图分类 + 槽位抽取评测（Intent Accuracy/F1 + Metric/Entity Recall）
  └── 服务启动时 warmup 语义向量，异常时自动降级到纯正则模式

层次 2：E2E 实战测评
  ├── 继承全局 agent_mode（single / multi）
  ├── 批量自动审批（普通工具自动通过，危险工具自动拒绝）
  └── 安全策略 + 审批机制完全复用生产链路

层次 3：CI/CD 门禁（Woodpecker + Smoke Eval）
  ├── 代码质量：Ruff / Oxlint / TypeScript 类型检查
  ├── 功能正确：Pytest（895 tests）/ Vitest（184 tests）
  └── Agent 行为：Golden Dataset 快检 → 路由/安全回归自动拦截
```

**回归门禁（Regression Gate）：**

8 条可解释规则，每条 finding 包含 rule / severity / case_id / baseline & candidate 值 / 人类可读 message：

| 规则 | 默认等级 | 含义 |
|------|----------|------|
| `pass_to_fail` | error | 基线通过 → 候选失败 |
| `safety_pass_to_fail` | error | 安全用例退化 |
| `forbidden_tool` | error | 调用禁用工具 |
| `missing_case` | error | 候选缺少基线 Case |
| `pass_rate_drop` | error | 通过率下降超阈值 |
| `latency_regression` | warning | 延迟退化 |
| `token_regression` | warning | Token 退化 |
| `fail_to_pass` | info | 用例改善 |

API 和 CLI **共用同一比较器**，避免"网页说通过，CI 说失败"的双重标准。

**其他反馈回路亮点：**

- **Trace Hub**：统一 `trace_id` → 父子 Span → 树形还原 → 耗时/Token/错误统计 → 敏感信息脱敏 → 前端时间脊柱展示
- **SBS（Side-by-Side）盲测**：同一提示词并行运行两套配置 → 盲化 A/B → 不可变评审 → NDJSON 导出
- **Replay Debugger**：Checkpoint 递归状态 Diff → 区分 added/removed/changed → Safe Fork（默认不执行）
- **Langfuse 集成**：opt-in 自动追踪 LLM/Tool/Graph 节点，线程级 session 关联

### 3. 约束机制 — ⭐⭐⭐⭐ 全面且可配置

**调用约束（Middleware 三件套）：**

- 单工具频率限制（50 次/轮）
- 总调用上限（20 次/轮）
- 死循环检测（15 次同参，20 窗口滑动）

**上下文约束：**

- 上下文阈值 1M token，超过 90% 或 20 轮对话触发压缩
- 压缩保留首尾消息，中间替换为 LLM 五段式摘要
- 工具结果用 `tool_result_id` 引用，可从 PostgreSQL 反查
- 完整对话存入 `.transcripts/` 目录供事后审计

**路径边界约束：**

- 文件操作限制在工作区范围内
- Shell 命令在沙箱工作区内执行

### 4. 工作流控制 — ⭐⭐⭐⭐ 编排能力扎实

**单 Agent 模式（ReAct 循环）：**

```
route_skills → compact_context → agent → memory_reflection → approval → tools → agent（循环）
```

**多 Agent 模式（APM 协作）：**

```
rewrite_intent（Hybrid 三层漏斗：正则 → BGE-M3 语义 → LLM）
→ retrieve_user_vector_context（Qdrant 可选）
→ supervisor（JSON 任务计划）
→ metrics / troubleshoot / patrol / audit（并行子 Agent）
→ synthesize（汇总子报告）
```

**状态管理：**

- LangGraph Checkpoint 持久化（PostgreSQL + Redis 双写）
- Checkpoint replay 可回放到任意节点
- 审批暂停/恢复状态机（SSE 推送审批卡片 → 用户决策 → 状态恢复）
- 多 Agent 结构化通信（JSON payload → 便于审计和评测）

### 5. 记忆与管理 — ⭐⭐⭐⭐ 分层清晰

**三层记忆体系：**

```
短期记忆：LangGraph checkpoint（PostgreSQL + Redis）
长期记忆：.memory/ USER.md / SYSTEM.md / MEMORY.md + PostgreSQL long_term_memories
知识记忆：RAG 知识库（Qdrant + Ollama BGE-M3）+ 混合检索（向量 + BM25 + RRF + LLM 过滤）
```

**上下文管理：**

- 上下文压缩：1M token 阈值 + 20 轮触发，LLM 摘要 + tool_result 引用
- 用户确认沉淀：后台 LLM 静默判断 → 前端非阻塞确认通知 → 落 Markdown + PostgreSQL
- 压缩前后端 SSE `compacting` 事件通知用户

---

## 三、不足之处

### 1. "人类掌舵"维度偏弱 — ⚠️ 核心差距

文章核心理念是**"人类掌舵，智能体执行"（Human Steer, Agent Execute）**，但项目目前的"掌舵"主要停留在审批层（Approve/Deny），缺少更主动的人类决策参与机制。

**具体表现：**

- **任务整备（东市买骏马）列为"扩展项方案，仅方案"**：用户输入直接丢给 Agent，没有"目标、约束、交付物、验证命令"四段式任务整备卡。这意味着人类在 Agent 开始工作前缺乏对任务范围的前置审核。
- **上下文装具（西市买鞍鞯）列为"扩展项方案，仅方案"**：没有将上下文来源打包为可审计的装备单。Agent 引用了哪些文档、历史会话、外部资料——缺乏前置透明展示。
- **执行缰绳（南市买辔头）列为"扩展项方案，仅方案"**：审批策略只有"批或不批"的二元选择。缺少文章描述的"可切换的缰绳等级"——例如严格模式、宽松模式、只读模式等分级的执行管控。
- **校阅追击（北市买长鞭）列为"扩展项方案，仅方案"**：缺少把测试结果、构建日志、审计事件汇总为战后校阅报告的自动化机制。

**评价：** 四个关键"人类掌舵"维度的功能全部标记为"仅方案，不在本次改造中新增"，这是一个明确的工程取舍——优先把 Agent 能力做深，人类主动控制做浅。但如果严格按照 Harness Engineering 的标准，这部分是较大差距。

### 2. 自愈能力有限 — ⚠️ 错误处理偏向"拦"而非"修"

文章提到 Harness Engineering 的核心目标之一是**"错误自愈：当 Agent 犯错时，系统能自动检测并纠正"**。

**项目当前状态：**

- **检测做得好**：Middleware 能检测到死循环、调用超限；Tool Guard 能检测到危险命令；Trace Hub 能检测到失败 Span
- **自动纠错弱**：
  - Agent 被 Middleware 拦截后，仅返回 `ToolMessage` 告知拦截原因，依赖 Agent"自己调整策略"
  - 如果 Agent 反复进入拦截循环（但每次参数不同，绕过 LoopDetection），没有更高级的干预
  - Multi-agent 模式下，子 Agent 失败后的恢复路径没有完整的降级和重试策略
  - 没有"如果连续 N 次被拦截，自动降级到只读模式"这样的自我保护机制

**具体场景：**

```
Agent 尝试 write_file → 被审批 → 用户拒绝
Agent 尝试 bash → 被审批 → 用户拒绝
Agent 尝试 write_file（不同路径）→ 被审批 → 用户拒绝
...
→ 没有"连续被拒 N 次后，Agent 应转为主要给建议而非操作"的策略
```

### 3. 成本与资源治理分散 — ⚠️ 覆盖面不够

文章描述 Harness Engineering 作为"基础设施"应关注全局资源控制。项目在这方面：

**已有：**
- 预算治理（incident-level budget policy + 线程/全局 Token 成本账本）
- OTEL 告警限流（分钟级）
- Middleware 工具调用次数限制

**缺失：**
- 没有全局的成本感知路由（例如：简单问题用小模型，复杂问题用大模型）
- 没有 Agent 运行时间/Token 的全局配额管理（跨线程预算）
- 没有按 Skill 粒度的成本追踪
- 前端没有成本可视化的实时展示

### 4. Harness 自身的可观测性不足 — ⚠️ "谁来监控监控器"

Harness 作为 AI Agent 的"外壳"，自身也需要可观测。当前：

**已有：**
- 安全事件审计日志
- 执行日志（turn / llm / tool / retry / approval）
- Trace Hub

**缺失：**
- Harness 自身各层的延迟分解（Prompt Guard 耗时 / Tool Guard 耗时 / Middleware 耗时 / Compaction 耗时）
- 上下文压缩的历史效果追踪（压缩前后的 Token 节省比率趋势）
- 审批拒绝率趋势（哪些工具被频繁拒绝，是否需要调整策略）
- Harness 组件的健康检查（Prompt Guard 的正则覆盖率是否足够？Tool Guard 的拦截率是否异常？）

### 5. 配置管理的工程化不够 — ⚠️ 缺乏 Harness Profile

项目使用环境变量管理配置，这在实际运维中容易产生问题：

- 没有 "Harness Profile" 概念（例如 `strict` / `permissive` / `development` 预设）
- 安全策略变更没有版本化回滚能力
- 跨环境的配置一致性依赖人工检查
- `SKILL_ROUTING_*`、`MULTI_AGENT_INTENT_*`、`PROMPT_GUARD_*` 等大量开关散落在环境变量中，缺少中心化的策略配置文件

### 6. Multi-Agent 协作的韧性不足 — ⚠️ 子 Agent 故障隔离不完整

多 Agent 编排中的故障隔离：

**已有：**
- supervisor 做 JSON 任务计划
- 子 Agent 并行执行
- 结构化 JSON 通信

**缺失：**
- 没有子 Agent 超时策略（如果子 Agent 卡住，supervisor 不知道等到什么时候）
- 没有子 Agent 结果验证（如果子 Agent 返回格式错误，缺少类型校验和重试）
- 没有子 Agent 的依赖关系管理（如果 metrics 的结果是 troubleshoot 的前置条件，应支持串行依赖）
- 没有子 Agent 的级联失败保护

---

## 四、对照 Harness Engineering 五组件的评分

| 组件 | 评分 | 说明 |
|------|------|------|
| **安全管控** | ⭐⭐⭐⭐⭐ | 多层纵深防御，正则+LLM 双层，CRITICAL/HIGH 分级，Middleware 三件套，Approval Gate 人工审批，审计日志完整。属于同类型项目的顶配。 |
| **反馈回路** | ⭐⭐⭐⭐⭐ | ClawEval 三层评测 + CI/CD 门禁 + Regression Gate 8 规则 + Trace Hub + SBS 盲测 + Replay Debugger。闭环完整性极高。 |
| **约束机制** | ⭐⭐⭐⭐ | Middleware 频率/总量/循环三层限流，上下文压缩自动触发，路径边界约束。缺少可切换的缰绳等级（仅有二元审批）。 |
| **工作流控制** | ⭐⭐⭐⭐ | LangGraph 状态图编排，Checkpoint 持久化，审批状态机，Multi-Agent 监督者模式。子 Agent 故障隔离和超时策略不足。 |
| **记忆与管理** | ⭐⭐⭐⭐ | 三层记忆体系清晰，上下文压缩成熟，长期记忆沉淀机制。缺少 Harness 自身的性能监控。 |

**总体评分：⭐⭐⭐⭐（4.2/5）**

---

## 五、总结

### 做得最好的：安全管控 + 反馈回路

这两项是项目最突出的亮点，也是 Harness Engineering 最核心的两块拼图。**安全管控**做到了"LLM 外面的层层防线，每一层独立兜底"；**反馈回路**做到了"从代码质量到 Agent 行为的全链路验证"，CI/CD + ClawEval 的组合在同类项目中属于一流水准。

### 最需要补的：人类掌舵 + 自愈能力

两个核心差距都指向同一个问题：**项目的 Harness 偏向"控"和"拦"，但"导"和"修"还不够。**

- **人类掌舵**：四个扩展项方案（任务整备/上下文装具/执行缰绳/校阅追击）全部标记为"仅方案"未实现。如果项目要向生产级 Harness Engineering 进阶，这些应该是下一阶段的核心任务。
- **自愈能力**：当前 Harness 更擅长"拦下错误"而非"从错误中恢复"。Agent 被拦截后的应对策略依赖 Agent 自身"聪明"，而非 Harness 主动引导。

### 一句话总结

**langgraph-claw 已经是一个非常好的 Harness Engineering 实践项目，在安全防御和可观测反馈方面达到了生产级水准。但要完全契合"人类掌舵，智能体执行"的 Harness Engineering 哲学，需要在人类主动决策参与和 Agent 错误自愈两个方向上做实质性补齐。**
