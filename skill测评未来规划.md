# Skill 测评体系未来规划

> 基于现有 `personal_assistant.skills.evaluation` 模块的能力盘点，规划 Skill 变更（新增/删除/修改）的完整评估闭环。

---

## 目录

- [1. 现状盘点](#1-现状盘点)
- [2. 核心问题](#2-核心问题)
- [3. 缺口分析](#3-缺口分析)
- [4. 四层评估体系](#4-四层评估体系)
- [5. 渐进实施路线](#5-渐进实施路线)
- [6. 各操作类型的评估矩阵](#6-各操作类型的评估矩阵)

---

## 1. 现状盘点

### 1.1 已建成模块

`backend/src/personal_assistant/skills/evaluation/` 提供了三个维度的量化评估：

| 维度 | 权重 | 度量内容 | 数据来源 | 对应模块 |
|------|------|----------|----------|----------|
| **Routing** | 40% | Skill 是否被正确路由命中（准确率 + 误报率） | Golden JSONL 用例集 | `offline.py` |
| **Runtime** | 30% | 线上执行成功率、重试率、P95/P99 延迟、Token 消耗 | 线上 ExecutionLog | `runtime.py` |
| **Static** | 20% | 描述简洁度、代码行数、圈复杂度、工具数量 | SKILL.md + skill.py | `static.py` |
| **Usage** | 10% | 该 Skill 被调用的绝对次数（归一化） | Runtime 日志 | `report.py` |

### 1.2 核心数据模型

```python
# Golden 用例 — 当前仅验证路由结果
class GoldenSkillCase:
    id: str
    query: str
    expected_skills: list[str]       # 仅验证路由
    expected_tool: str | None        # 预留但未使用
    expected_args: dict | None       # 预留但未使用

# 综合报告
class SkillEvaluationReport:
    skills: list[SkillEvaluationResult]   # 每个 Skill 的独立评分
    routing: RoutingMetrics | None        # 整体路由质量
```

### 1.3 现有框架定位

**"当前状态的体检报告"**（snapshot），而非 **"变更影响评估"**（delta analysis）。

---

## 2. 核心问题

> 新增、删除、修改一个 Skill 对系统是否是增益的？

现有框架回答的是「每个 Skill 现在多少分」，而实际需要回答的是：

- **新增**：新 Skill 是否填补了能力空白？是否干扰已有 Skill 的路由？自身质量过关吗？
- **删除**：被删 Skill 是否真的无人使用？删除后依赖它的 Golden Cases 会怎样？
- **修改**：修改后路由准确率是升是降？代码质量变好还是变差？是否引入回归？

这需要从「单次体检」升级为 **「变更前后对比 + 回归阻断 + 趋势追踪」** 的完整闭环。

---

## 3. 缺口分析

### 缺口 1：缺少「基线对比」机制

`evaluate_skill_registry()` 只告诉你**现在**每个 Skill 得分多少。要评估变更效果，需要一个：

```
baseline snapshot → change → new snapshot → diff
```

的工作流。`SkillEvaluationReport` 已是 Pydantic 模型，两次运行的 JSON 可以直接做 diff——缺的是自动化这个流程的 CLI 和 CI 集成。

### 缺口 2：Golden Dataset 只覆盖路由，不覆盖端到端行为

当前 `offline.py` 只调用了 `route_skill_names()`，验证「query → skill name」的映射。但 Skill 被激活后 Agent 的实际行为完全不可见：

- Skill instructions 写得太宽泛 → Agent 乱调工具
- Skill 新增 tool 和已有 tool 名冲突
- Skill description 太「吸引人」 → false positive 提升

这些问题在当前 Golden Dataset 中**完全不可见**。

### 缺口 3：缺少跨 Skill 交互影响分析

新增一个 Skill 不是孤立事件，它会改变整个路由空间：

| 效应 | 描述 | 检测手段 |
|------|------|----------|
| **语义干扰** | 新 Skill 的 description embedding 和已有 Skill 过于接近 → router 在两个之间摇摆 | 计算 Skill 间 cosine similarity |
| **路由竞争** | 新 Skill 的 triggers/description 关键词和已有 Skill 重叠 → regex 层匹配顺序决定输赢 | 对同一 query 查看多层路由的中间结果 |
| **Token 预算挤压** | 每个选中 Skill 的 instructions 塞进 system prompt → Skill 越多，单个 Skill 能用的 context 越少 | System prompt token 统计 |

### 缺口 4：Runtime 冷启动问题

新 Skill 加入时 `tool_calls = 0` → Usage 维度 0 分，`execution_success_rate = None` → Runtime 维度权重被跳过。现有评分公式会**系统性低估新 Skill**，需要区分「数据不足」和「质量差」。

### 缺口 5：缺少 CI Gate 语义

Golden Dataset 应该是**只增不减**的累积资产。每新增一个 Skill 应同时新增 Golden Cases；每次修改 Skill，**全量 Golden Cases 必须全部通过**。现有框架有能力跑全量，但缺少明确的阻断规则和 CI 集成。

---

## 4. 四层评估体系

```
┌──────────────────────────────────────────────────────────────┐
│                      Skill 变更评估闭环                       │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  第一层：回归安全网 (Regression Gate)                         │
│  ├─ 累积式 Golden Dataset（只增不减）                         │
│  ├─ 变更前保存 baseline → 变更后 diff                         │
│  ├─ 阻断条件：accuracy 下降 / false_positive 上升 / score 骤降 │
│  └─ CI 集成：PR 自动触发全量 Golden 评估                      │
│                                                              │
│  第二层：准入评估 (Admission Gate)                            │
│  ├─ 新 Skill 必须附带 ≥5 条 Golden Cases                      │
│  ├─ Static 硬性门槛（description_tokens < 200, complexity < 15）│
│  ├─ 语义重叠检查（与已有 Skill 的 cosine similarity < 0.85）   │
│  └─ 全量 Golden Cases 零回归验证                              │
│                                                              │
│  第三层：线上追踪 (Runtime Monitoring)                        │
│  ├─ 每个 Skill 的 Runtime 指标时间序列（非单次 snapshot）      │
│  ├─ 定时快照写入 DB（每日一次）                                │
│  ├─ 趋势告警：success_rate 跌破 95% / latency 翻倍             │
│  └─ 冷启动期特殊标记（前 100 次调用标注为「预热中」）           │
│                                                              │
│  第四层：端到端行为评估 (E2E Quality Gate) [中长期]            │
│  ├─ 扩展 Golden Case：加 expected_tool_calls / expected_answer │
│  ├─ 完整运行 Agent（不只是 router）                            │
│  └─ Nightly build / PR optional check                         │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 4.1 第一层：回归安全网

**目标**：任何变更不得破坏已有能力。

**机制**：
1. 维护**累积式 Golden Dataset**（`golden_cases.jsonl`），每个 Skill 贡献 5-10 个 case
2. 变更前跑一次全量评估 → 保存为 baseline JSON
3. 变更后跑一次 → 生成 diff 报告
4. **阻断条件**（CI 不通过）：
   - 任何已有 Skill 的 `selection_accuracy` 下降
   - 整体 `false_positive_rate` 上升
   - 任何已有 Skill 的 `overall_score` 下降超过 5%

**现有框架覆盖率**：80%。`offline.py` 的逐条对比逻辑已经正确，需补充的是 diff 计算和 CI 集成。

### 4.2 第二层：新 Skill 准入评估

**目标**：新 Skill 在合并前证明自身价值。

**机制**：
1. PR 必须附带 Golden Cases（覆盖：正向命中 + 边界条件 + 不应命中的 negative case）
2. 运行**全量** Golden Cases（不仅是新增的）——验证不干扰已有路由
3. **Static 硬性门槛**：
   - `description_tokens` ≤ 200（描述简洁，避免语义漂移）
   - `max_cyclomatic_complexity` ≤ 15（代码不过于复杂）
   - `tool_count` ≥ 1（至少做一件事）
4. **语义重叠检查**（需新增 `overlap.py`）：
   - 用已有 embedding provider 计算新 Skill description 与所有已有 Skill description 的 cosine similarity
   - 超过 0.85 → 警告可能存在路由冲突，建议修改 description 做差异化

**现有框架覆盖率**：60%。Static 指标已有，全量 Golden 可跑。缺「语义重叠检查」和「附带 Golden Cases 的流程约束」。

### 4.3 第三层：线上追踪

**目标**：变更上线后持续观察，确认实际效果。

**机制**：
1. 对每个 Skill 维护**运行时指标的时间序列**（而非单次 snapshot）
2. 定时（如每日）跑 `evaluate_runtime_logs()` 并将结果写入 DB
3. 变更上线后观察趋势：
   - `execution_success_rate` 是否稳定在 95%+
   - `retry_ratio` 是否在下降
   - `p95_latency_ms` 是否在可接受范围
4. 趋势恶化 → 触发 rollback 告警
5. **冷启动特殊处理**：新 Skill 前 100 次调用标注为「预热中」，不触发低分告警

**现有框架覆盖率**：40%。Runtime 指标计算逻辑已有（`runtime.py`），缺时序存储和趋势告警。`test_skill_evaluation.py` 中已有 `SkillEvaluationSnapshot` 的持久化路径（`_run_skill_evaluation_and_persist`），方向已有雏形。

### 4.4 第四层：端到端行为评估（中长期）

**目标**：验证 Skill 不只是被路由到，而是**真正完成了用户的任务**。

**机制**：扩展 Golden Dataset 概念：
```python
class EndToEndGoldenCase:
    id: str
    query: str
    expected_skills: list[str]
    # 新增字段
    expected_tool_calls: list[dict] | None    # 期望的工具调用序列
    expected_answer_contains: list[str] | None # 期望回答中包含的关键内容
    # 完整运行 Agent，对比输出
```

这比纯路由测试昂贵（每次都要真正跑 Agent + LLM 推理），作为**低频异步任务**：nightly build 或 PR 的 optional check。

**现有框架覆盖率**：10%。只有路由层，没有 Agent 执行层。

---

## 5. 渐进实施路线

### 短期（流程层面，无需改框架代码）

| 事项 | 做法 | 价值 |
|------|------|------|
| **变更前后对比工作流** | 变更前 `--output-json baseline.json`，变更后再跑一次，diff 对比 | 立即获得回归检测能力 |
| **Golden Dataset 积累规范** | 每新增/修改 Skill 强制追加 ≥5 条 case 到 `golden_cases.jsonl` | 测试集随时间增长，覆盖越来越全 |
| **Diff 关注项清单** | `selection_accuracy` 下降 → 阻断；`overall_score` 下降 > 0.05 → 警告；`false_positive_rate` 上升 → 警告 | 明确的决策依据 |

**操作示例**：
```bash
# 变更前
python -m personal_assistant.skills.evaluation \
  --skills-dir src/personal_assistant/skills \
  --golden golden_cases.jsonl \
  --output-json baseline.json

# ... 做出 Skill 变更 ...

# 变更后
python -m personal_assistant.skills.evaluation \
  --skills-dir src/personal_assistant/skills \
  --golden golden_cases.jsonl \
  --output-json after.json

# 对比
diff <(jq -S . baseline.json) <(jq -S . after.json)
```

### 中期（需要少量开发，约 3-5 天）

| 能力 | 改动范围 | 依赖 |
|------|----------|------|
| **语义重叠检测** | 新增 `evaluation/overlap.py`，用已有 `SkillEmbeddingProvider` 计算 Skill 间 cosine similarity，输出高重叠对列表 | `OllamaBgeM3EmbeddingProvider`（已有） |
| **基线对比 CLI** | 给 `__main__.py` 加 `--baseline` 参数，接受之前的 JSON 文件路径，自动输出 diff 报告（Markdown 格式，高亮变化项） | `report.py` 的渲染能力 |
| **Runtime 定时快照** | 后台定时任务（APScheduler 或简单 asyncio loop），每天调用 `evaluate_runtime_logs()` + 结果写入 `skill_evaluation_snapshots` 表 | PostgreSQL（已有） |
| **CI 集成脚本** | `.github/workflows/skill-eval.yml`，PR 触发，跑全量 Golden，输出 PR comment | GitHub Actions |

### 长期（架构演进，按需推进）

| 能力 | 描述 |
|------|------|
| **端到端 Golden Cases** | 扩展 `GoldenSkillCase` 支持 `expected_tool_calls` 和 `expected_answer_contains`，运行完整 Agent 验证 |
| **A/B 对比模式** | 对同一批 query 同时跑旧版和新版 Skill 配置，对比路由结果和任务完成率 |
| **Skill 版本管理** | 每个 Skill 保留多版本，runtime 指标按版本分组，支持快速回滚到历史版本 |
| **自动告警** | Runtime 指标恶化（成功率跌破阈值、延迟翻倍）时自动推送通知 |

---

## 6. 各操作类型的评估矩阵

### 6.1 新增 Skill

```
┌──────────────┬────────────────────────────────┬──────────┐
│    评估项     │           方法                 │   层级    │
├──────────────┼────────────────────────────────┼──────────┤
│ 覆盖增益      │ 新 Golden Cases 全部命中        │ 第二层   │
│ 零回归        │ 全量旧 Golden Cases 通过         │ 第一层   │
│ 语义冲突      │ cosine similarity 检查          │ 第二层   │
│ 静态质量      │ description_tokens < 200,       │ 第二层   │
│              │ complexity < 15, tool_count ≥ 1  │          │
│ 线上表现      │ 上线后观察 7 天 runtime 指标     │ 第三层   │
│ 端到端有效性  │ E2E Golden Cases（中长期）       │ 第四层   │
└──────────────┴────────────────────────────────┴──────────┘
```

### 6.2 删除 Skill

```
┌──────────────┬────────────────────────────────┬──────────┐
│    评估项     │           方法                 │   层级    │
├──────────────┼────────────────────────────────┼──────────┤
│ 使用频率      │ 检查 Runtime Usage 数据        │ 第三层   │
│ 依赖检查      │ 是否有 Golden Cases 依赖此 Skill │ 第一层   │
│ 零回归        │ 删除后全量 Golden Cases 通过    │ 第一层   │
│ 能力缺口      │ 是否有 query 类型失去覆盖       │ 第一层   │
└──────────────┴────────────────────────────────┴──────────┘
```

### 6.3 修改 Skill

```
┌──────────────┬────────────────────────────────┬──────────┐
│    评估项     │           方法                 │   层级    │
├──────────────┼────────────────────────────────┼──────────┤
│ 路由回归      │ baseline vs after diff         │ 第一层   │
│ 静态质量变化  │ complexity / lines / tokens diff│ 第二层   │
│ Bug 修复验证  │ 追加回归 Golden Case            │ 第一层   │
│ 线上对比      │ 修改前后 7 天 runtime 指标对比   │ 第三层   │
│ 语义漂移      │ 修改后与相邻 Skill 的重叠检查    │ 第二层   │
└──────────────┴────────────────────────────────┴──────────┘
```

---

## 总结

现有 `evaluation` 模块是一个很好的「体检仪」——能告诉你每个 Skill 当前的健康状况。但它缺少的是**时间维度**（变更前后的对比）和**关系维度**（Skill 之间的交互影响）。

核心思路不是推翻重建，而是在现有框架上补三个关键能力：

1. **Diff 引擎**：baseline vs after 的自动化对比，让「变更是否增益」从主观判断变为客观指标
2. **语义重叠检测**：用已有的 embedding 基础设施，在准入阶段就发现潜在的路由冲突
3. **时序追踪**：将 Runtime 快照定时持久化，让新 Skill 的线上表现从「看不到」变为「可追踪」

80% 的代码能力已经在 `evaluation/` 目录里了，缺的主要是流程、对比逻辑、和语义重叠检测三个补丁。
