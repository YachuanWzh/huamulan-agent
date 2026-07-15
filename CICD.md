# langgraph-claw CI/CD 与快检全流程

> **版本**: 1.0.0  
> **状态**: ✅ 已跑通全流程  
> **CI 引擎**: Woodpecker CI  
> **快检引擎**: ClawEval（内置 Skill/Agent 评测系统）

---

## 目录

1. [概述](#1-概述)
2. [流水线架构](#2-流水线架构)
3. [各阶段详解](#3-各阶段详解)
4. [快检系统（ClawEval）](#4-快检系统claweval)
5. [Golden Dataset 体系](#5-golden-dataset-体系)
6. [本地运行与开发调试](#6-本地运行与开发调试)
7. [流水线触发规则](#7-流水线触发规则)
8. [最佳实践](#8-最佳实践)

---

## 1. 概述

langgraph-claw 采用 **Woodpecker CI + ClawEval 快检** 双引擎，实现"提交即检查、合并即验证"的自动化质量门禁。整个 CI/CD 流程覆盖三大维度：

| 维度 | 手段 | 说明 |
|------|------|------|
| **代码质量** | Ruff Lint / Oxlint / TypeScript 类型检查 | 静态分析，零成本拦截低级错误 |
| **功能正确性** | Pytest / Vitest 单元测试 | 后端 + 前端双线并行测试 |
| **Agent 行为** | ClawEval 快检（Smoke Eval） | 用 Golden Dataset 验证 Agent 路由、意图分类、安全拦截是否退化 |

通俗地讲，就是你每提交一次代码，CI 会依次帮你做三件事：**检查代码写得规不规范** → **跑单元测试看有没有改坏** → **用几十条标准用例考考 Agent 还能不能正确回答问题**。全绿才能放心合入 master。

---

## 2. 流水线架构

```mermaid
flowchart TB
    subgraph TRIGGER["触发条件"]
        PUSH["Git Push<br/>master / main 分支"]
        PR["Pull Request"]
        MANUAL["手动触发"]
    end

    PUSH --> CI
    PR --> CI
    MANUAL --> CI

    subgraph CI["Woodpecker CI Pipeline"]
        direction TB

        subgraph BACKEND["后端流水线 (python:3.12-slim)"]
            LINT_BE["🔍 lint-backend<br/>Ruff 代码风格检查"]
            TEST_BE["🧪 test-backend<br/>Pytest 单元测试<br/>(跳过 integration 标记)"]
            LINT_BE --> TEST_BE
        end

        subgraph FRONTEND["前端流水线 (node:22-alpine)"]
            LINT_FE["🔍 lint-frontend<br/>Oxlint 代码检查"]
            TC_FE["📐 typecheck-frontend<br/>TypeScript 类型检查"]
            TEST_FE["🧪 test-frontend<br/>Vitest 单元测试"]
            BUILD_FE["📦 build-frontend<br/>生产构建验证"]
            LINT_FE --> TC_FE --> TEST_FE --> BUILD_FE
        end

        subgraph SMOKE["Smoke Eval (快检)"]
            EVAL["🔥 smoke-eval<br/>ClawEval 路由快检<br/>仅 backend 变更时触发"]
        end
    end

    BACKEND --> SMOKE
    FRONTEND --> SMOKE
```

**关键设计**：

- 后端和前端的 Lint/Test 阶段**并行执行**，互不阻塞，最大化利用 CI 资源；
- `smoke-eval` 快检阶段在整个流水线最后执行，只有 `backend/**` 或 `.woodpecker.yml` 变更时才触发（`.md` 和 `docs/` 变更自动跳过），避免不必要的 LLM 调用成本；
- 前端构建 (`build-frontend`) 确保代码不仅能通过类型检查，还能成功产出生产包。

---

## 3. 各阶段详解

### 3.1 lint-backend — 后端代码风格检查

```yaml
lint-backend:
  image: python:3.12-slim
  commands:
    - pip install --quiet uv
    - cd backend
    - uv pip install --system ruff
    - ruff check src/ tests/
```

- **工具**: [Ruff](https://docs.astral.sh/ruff/)（极速 Python Linter）
- **检查范围**: `backend/src/` 和 `backend/tests/` 下所有 Python 文件
- **规则集**: Ruff 默认规则 + 项目配置（`pyproject.toml` 中 `[tool.ruff]` 段）
- **失败处理**: 任何 lint 错误都会导致 CI 失败，必须修复后才能继续

### 3.2 test-backend — 后端单元测试

```yaml
test-backend:
  image: python:3.12-slim
  commands:
    - pip install --quiet uv
    - cd backend
    - uv pip install --system -e ".[dev]"
    - python -m pytest -v -m "not integration" --timeout=120
```

- **测试框架**: Pytest + pytest-asyncio（异步测试支持）
- **运行策略**: 跳过 `integration` 标记的测试（CI 环境无 PostgreSQL/Redis/Qdrant 等中间件），单测超时 120 秒
- **环境变量**: 注入 mock 配置（`DATABASE_URL`、`LLM_MODEL`、`OTEL_*` 等），确保测试不依赖外部服务

### 3.3 lint-frontend — 前端代码检查

```yaml
lint-frontend:
  image: node:22-alpine
  directory: frontend
  commands:
    - npm ci
    - npm run lint
```

- **工具**: [Oxlint](https://oxc.rs/docs/guide/usage/linter.html)（基于 Rust 的超快 JavaScript/TypeScript Linter）
- **检查范围**: `frontend/src/` 下所有 `.ts`/`.tsx` 文件

### 3.4 typecheck-frontend — TypeScript 类型检查

```yaml
typecheck-frontend:
  image: node:22-alpine
  directory: frontend
  commands:
    - npm ci
    - npx tsc --noEmit
```

- 只做类型推导和检查，不输出编译产物（`--noEmit`）
- TypeScript 6.0 严格模式，确保整个前端代码库类型安全

### 3.5 test-frontend — 前端单元测试

```yaml
test-frontend:
  image: node:22-alpine
  directory: frontend
  commands:
    - npm ci
    - npm test -- --maxWorkers=1
```

- **测试框架**: Vitest 4.x + @testing-library/react
- **API Mock**: MSW (Mock Service Worker) 拦截所有 HTTP 请求
- **单 Worker 模式**: Alpine 镜像资源有限，`--maxWorkers=1` 避免资源争抢

### 3.6 build-frontend — 前端生产构建

```yaml
build-frontend:
  image: node:22-alpine
  directory: frontend
  commands:
    - npm ci
    - npm run build
```

- 执行 `tsc -b && vite build`，确保代码能成功编译打包
- 这是类型检查之外的第二道防线：有些运行时问题只在构建阶段暴露

### 3.7 smoke-eval — 快检（Smoke Eval）

这是整个 CI/CD 流程中最具"AI 项目特色"的阶段——**用标准用例验证 Agent 行为没有退化**。

```yaml
smoke-eval:
  image: python:3.12-slim
  when:
    path:
      include: ["backend/**", ".woodpecker.yml"]
      exclude: ["*.md", "docs/**"]
  commands:
    - pip install --quiet uv
    - cd backend
    - uv pip install --system -e ".[dev]"
    - uv run python -m personal_assistant.skills.evaluation \
        --skills-dir src/personal_assistant/skills \
        --golden evaluation/golden/otel_routing.jsonl \
        --llm-base-url "$LLM_BASE_URL" \
        --llm-model "$LLM_MODEL" \
        --output-md ../eval-report.md
    - cat ../eval-report.md
```

**工作原理**：

```mermaid
flowchart LR
    subgraph INPUT["输入"]
        GOLDEN["Golden Dataset<br/>otel_routing.jsonl<br/>数十条标准问法"]
        SKILLS["Skill Registry<br/>resolvetime / troubleshoot<br/>patrol / audit-sop / ..."]
    end

    subgraph EVAL_ENGINE["ClawEval 评估引擎"]
        ROUTE["三层漏斗路由<br/>1️⃣ 正则匹配<br/>2️⃣ 语义检索 (BGE-M3)<br/>3️⃣ LLM 判定"]
        COMPARE["对比期望 vs 实际"]
    end

    subgraph OUTPUT["输出"]
        REPORT["eval-report.md<br/>路由准确率 / 误触发率<br/>每 Skill 静态评分"]
    end

    GOLDEN --> ROUTE
    SKILLS --> ROUTE
    ROUTE --> COMPARE
    COMPARE --> REPORT
```

**关键配置**：

- 使用 DeepSeek API 做 LLM 判定（第三层漏斗），确保与生产环境行为一致；
- `OPENAI_API_KEY` 从 Woodpecker Secret 注入，不硬编码在配置文件中；
- 仅当 `backend/**` 变更时触发，纯前端或文档变更自动跳过；
- 报告打印到 CI 日志并生成 `eval-report.md`，便于事后追溯。

---

## 4. 快检系统（ClawEval）

快检是 langgraph-claw 内置的 **Agent 行为回归测试系统**，设计目标不是"代码跑通"，而是"Agent 还能正确理解用户、选中技能"。

### 4.1 快检 vs 完整评测

| 维度 | 快检 (Smoke Eval) | 完整评测 (Full Eval) |
|------|-------------------|----------------------|
| **运行场景** | CI 流水线自动触发 | 本地手动或前端 UI 触发 |
| **评测内容** | 路由准确率 + Skill 静态评分 | 路由 + 工具调用 + 答案合规 + 安全拦截 |
| **数据量** | 精选 subset（~数十条） | 全量 Golden Dataset（515 条） |
| **是否调 LLM** | 是（第三层判定） | 是 |
| **耗时** | < 2 分钟 | 5-30 分钟（取决于模式） |
| **阻塞 CI** | 是 | 否（本地执行） |

### 4.2 三层漏斗路由

快检使用与生产**完全一致**的路由逻辑，确保测试结果可信：

```mermaid
flowchart TD
    Q["用户输入<br/>'帮我看下最近 trace 延迟'"] --> T1

    T1["🔹 Tier 1: 正则匹配<br/>匹配 SKILL.md frontmatter triggers<br/>例如 'trace' → otel-query Skill"]
    T1 -->|"命中"| DONE["✅ 返回 Skill"]
    T1 -->|"未命中"| T2

    T2["🔹 Tier 2: 语义检索<br/>BGE-M3 embedding + Qdrant 向量搜索<br/>余弦相似度 ≥ 0.72 命中"]
    T2 -->|"命中"| DONE
    T2 -->|"未命中或低分"| T3

    T3["🔹 Tier 3: LLM 判定<br/>把 query 和候选 Skill 交给 LLM 判断<br/>返回 confidence + reason"]
    T3 -->|"≥ 阈值"| DONE
    T3 -->|"低于阈值"| NONE["⚠️ 不匹配任何 Skill"]
```

### 4.3 前端快检面板

在项目前端界面的 "军械" → "Skill Evaluation" 页面：

1. 从下拉框选择 Golden Dataset（`otel_routing` / `golden_dataset` / `skill_routing` 等）；
2. 点击 **快速巡检**（Quick）按钮，使用三层漏斗路由验证 Skill 选择；
3. 点击 **实战测评**（E2E）按钮，跑完整 Agent 链路验证端到端行为。

---

## 5. Golden Dataset 体系

Golden Dataset 是"标准答案集"——由人工或 LLM 辅助构建的测试用例，每条用例包含用户输入和期望的 Agent 行为。

### 5.1 数据集一览

| 数据集文件 | 用例数 | 用途 | 快检使用 |
|-----------|--------|------|----------|
| `otel_routing.jsonl` | ~40 条 | OTEL 遥测场景路由覆盖 | ✅ CI 快检 |
| `golden_dataset.jsonl` | ~100 条 | 通用 Skill 路由与静态评分 | 可选 |
| `skill_routing.jsonl` | ~80 条 | 专项路由覆盖测试 | 可选 |
| `claw_eval_smoke.jsonl` | ~10 条 | 最小冒烟集 | 可选 |
| `e2e_dataset.jsonl` | ~50 条 | 端到端 Agent 行为 | 可选 |
| `trouble_dateset.jsonl` | ~30 条 | 排障场景 | 可选 |
| `apm_knowledge.jsonl` | ~30 条 | APM 知识查询 | 可选 |
| `apm_patrol.jsonl` | ~30 条 | 巡检场景 | 可选 |
| `apm_runbook.jsonl` | ~30 条 | 运行手册场景 | 可选 |
| `apm_troubleshooting.jsonl` | ~30 条 | APM 排障场景 | 可选 |
| `apm_realistic.jsonl` | ~30 条 | APM 实战综合 | 可选 |
| `governance_audit.jsonl` | ~20 条 | 治理审计场景 | 可选 |
| `security_prompt_guard.jsonl` | ~25 条 | 安全防护专项 | 可选 |

> **总计 515 条用例**，覆盖路由、安全、APM、审计、排障等全部场景。

### 5.2 用例格式

**基础格式**（Quick 快检用）：

```json
{"id": "otel-001", "query": "查看最近 trace 的延迟", "expected_skills": ["otel-query"]}
{"id": "negative-001", "query": "写一首诗", "expected_skills": []}
```

**完整格式**（E2E 实战测评用）：

```json
{
  "id": "weather-e2e-001",
  "turns": ["我在杭州", "查一下未来天气"],
  "expected_skills": ["weather"],
  "expected_tool_calls": [{"name": "weather", "args": {"city": "杭州"}}],
  "expected_answer_contains": ["杭州"],
  "forbidden_tools": ["shell_command"]
}
```

---

## 6. 本地运行与开发调试

### 6.1 本地跑 CI 全流程

```powershell
# 后端 Lint
cd backend
uv pip install --system ruff
ruff check src/ tests/

# 后端测试
uv pip install --system -e ".[dev]"
python -m pytest -v -m "not integration" --timeout=120

# 前端 Lint
cd frontend
npm ci
npm run lint

# 前端类型检查
npx tsc --noEmit

# 前端测试
npm test -- --maxWorkers=1

# 前端构建
npm run build
```

### 6.2 本地跑快检

```powershell
cd backend
uv run python -m personal_assistant.skills.evaluation \
  --skills-dir src/personal_assistant/skills \
  --golden evaluation/golden/otel_routing.jsonl \
  --llm-base-url "https://api.deepseek.com" \
  --llm-model "deepseek-chat" \
  --output-md eval-report.md
```

### 6.3 本地用 Woodpecker CLI

如果安装了 [Woodpecker CLI](https://woodpecker-ci.org/docs/cli)：

```powershell
woodpecker-cli exec local
```

这会在本地 Docker 环境中完整模拟 CI 流水线。

---

## 7. 流水线触发规则

```yaml
when:
  branch: [master, main]
  event: [push, pull_request, manual]
```

| 触发方式 | 说明 |
|----------|------|
| **Push to master/main** | 合入主分支后自动触发，确保主干始终绿 |
| **Pull Request** | 提交 PR 时自动触发，作为合入门禁 |
| **手动触发** | Woodpecker UI 点击 "Run Pipeline" 手动执行 |

**快检的条件触发**：

```yaml
smoke-eval:
  when:
    path:
      include: ["backend/**", ".woodpecker.yml"]
      exclude: ["*.md", "docs/**"]
```

- 只改前端代码？**不跑快检**，省 LLM 费用；
- 只改文档？**不跑快检**；
- 改了后端代码或 CI 配置？**必须跑快检**。

---

## 8. 最佳实践

### 开发流程建议

```mermaid
flowchart LR
    DEV["💻 本地开发"] --> LINT["📏 本地 Lint + Test"]
    LINT --> QUICK["⚡ 本地快检<br/>(可选，改路由/Skill 时必须)"]
    QUICK --> PUSH["⬆️ git push"]
    PUSH --> CI["🔄 CI Pipeline"]
    CI -->|全绿| MERGE["✅ 合入 master"]
    CI -->|失败| FIX["🔧 修复后重新推送"]
    FIX --> PUSH
```

### 新增 Skill 时的检查清单

1. 写 `SKILL.md`，声明 triggers 和 scripts；
2. 在 Golden Dataset 中新增对应测试用例（要包含**正例**——应该匹配的和**负例**——不应该匹配的）；
3. **本地跑快检**确认路由正确：
   ```powershell
   cd backend
   uv run python -m personal_assistant.skills.evaluation \
     --skills-dir src/personal_assistant/skills \
     --golden evaluation/golden/otel_routing.jsonl \
     --llm-base-url "https://api.deepseek.com" \
     --llm-model "deepseek-chat"
   ```
4. 提交时 CI 快检会自动验证。

### 快检失败怎么办

快检失败通常有以下几种情况：

| 现象 | 可能原因 | 解法 |
|------|----------|------|
| `Selection Accuracy` 下降 | Skill 的 triggers 写得太宽/太窄 | 调整 `SKILL.md` frontmatter triggers |
| `False Positive Rate` 升高 | 某个 Skill 误匹配了不该匹配的输入 | 增加负例用例，收窄 trigger 正则 |
| 语义检索命中率下降 | BGE-M3 embedding 模型变化或 Skill 描述大变 | 检查 Qdrant 索引，必要时 reload |
| LLM 判定层频繁触发 | 正则和语义层都未能短路 | 考虑增加正则 trigger 或优化 Skill 描述 |

---

## 附录：CI 环境变量速查

| 变量 | 值 | 说明 |
|------|-----|------|
| `PYTHONUNBUFFERED` | `1` | Python 输出不缓冲，CI 日志实时可见 |
| `PIP_INDEX_URL` | `https://mirrors.aliyun.com/pypi/simple/` | 阿里云 PyPI 镜像，加速安装 |
| `UV_DEFAULT_INDEX` | `https://mirrors.aliyun.com/pypi/simple/` | uv 包管理器镜像 |
| `DATABASE_URL` | `postgresql://test:test@127.0.0.1:5432/test` | 测试数据库连接（仅 test-backend 使用） |
| `LLM_MODEL` | `test-model` / `deepseek-chat` | 测试用 mock 模型 / 快检用真实模型 |
| `OPENAI_API_KEY` | *(from_secret)* | Woodpecker Secret，用于快检 LLM 调用 |

---

> 📘 技术架构详见 [技术方案报告.md](./技术方案报告.md)  
> 📦 项目总览详见 [README.md](./README.md)  
> 🤖 开发规范详见 [CLAUDE.md](./CLAUDE.md)
