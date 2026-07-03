# s03: Skill Loading（技能加载）

`[ s01 > s02 ] s03 > s04 > s05 > ...`

> *"No full read until you need it"* —— 扫描时只要 YAML frontmatter，正文等路由命中后再加载。
> **Harness 层**: 知识管理 —— 技能系统

## 问题

s02 的 agent 已经有了一套通用工具（bash, read_file, write_file），但它是"笨"的——
不会根据用户意图切换行为模式。当用户问"北京今天天气怎么样"，agent 不知道该
去哪里查天气、用什么格式返回、有哪些注意事项。

你需要一个**技能系统**：让 agent 能按需加载领域知识（instructions），在正确的
时机激活正确的行为模式。

但技能多了就有新问题：如果启动时把所有 SKILL.md 的正文都读进内存，启动慢、
浪费内存（大部分技能根本不会被用到）。因此需要**两阶段加载**——先扫元数据
建索引（快），命中后再读正文（省）。

## 解决方案

```
                    skills/
                   ├── weather/
                   │   └── SKILL.md    ← YAML frontmatter + Markdown body
                   ├── audit-sop/
                   │   └── SKILL.md
                   └── find-skills/
                       └── SKILL.md

  Phase 1 (scan)                     Phase 2 (on-demand)
  ──────────────                     ────────────────────
  for each SKILL.md:                  load_skill("weather")
    parse YAML frontmatter only  →      read full Markdown body
    build {name, desc, triggers}        return instructions
  → skill index (dict)                 → full skill ready
```

核心组件是 `SkillRegistry`：

- **Phase 1 — `scan_metadata()`**: 遍历 `skills/` 下的每个子目录，找到 `SKILL.md`，
  只解析 `---` 包裹的 YAML frontmatter（name, description, triggers），不读正文。
- **Phase 2 — `load_skill(name)`**: 当技能被选中时，从磁盘读取完整 Markdown 正文，
  作为该技能的 instructions 注入上下文。
- 每个技能是一个**自包含包**：`SKILL.md`（指令） + 可选的 `scripts/` 子目录。

## 工作原理

### 1. SKILL.md 格式

每个技能目录下放一个 `SKILL.md`，结构为 **YAML frontmatter + Markdown 正文**：

```markdown
---
name: weather
description: 查询指定城市的当前天气和未来预报
triggers:
  - 天气
  - 气温
  - 下雨
---

# Weather — 天气查询

当用户询问天气时，使用 wttr.in API 获取实时数据...
```

- `name`: 技能唯一标识（默认用目录名）
- `description`: 一句话描述，用于技能索引和路由匹配
- `triggers`: 触发词列表，用于 Phase 1 扫描后注入 SystemMessage，帮助 LLM 做语义匹配
- Markdown 正文: 完整的使用说明、注意事项、示例——Phase 2 按需加载

### 2. 两阶段加载流程

```
Agent 启动
  │
  ▼
SkillRegistry.__init__()
  │
  ├── scan_metadata()           ← Phase 1: 毫秒级
  │     for d in skills/:
  │       meta = parse_frontmatter(d/SKILL.md)
  │       skills[d.name] = Skill(name, desc, triggers)
  │
  ▼
build_system_message()
  │  注入 Skill 摘要到 SystemMessage:
  │  "- weather: 天气查询 [触发词: 天气, 气温, ...]"
  │
  ▼
用户: "北京今天天气怎么样"
  │
  ▼
agent_node 看到 SystemMessage 中的技能列表
  │  LLM 发现 weather 技能匹配 → 调用工具 load_skill("weather")
  │
  ▼
load_skill("weather")           ← Phase 2: KB 级读取
  │  skill.instructions = read_text(SKILL.md 正文)
  │  return full instructions
  │
  ▼
Agent 获得完整天气查询指南 → 执行任务
```

### 3. 为什么两阶段

| | 全部预加载 | 两阶段加载 |
|---|---|---|
| 启动时间 | O(N) 文件读取 | Phase 1 只读 frontmatter（每文件 5-10 行） |
| 内存占用 | 所有技能正文常驻 | 只保留被激活的技能正文 |
| 典型场景 | 50 个技能，30 个永不用 | Phase 1: 50 个 frontmatter，Phase 2: 只加载 2-3 个 |
| 技能热更新 | 需要全量重载 | 重新 scan_metadata 即可，loaded 技能保持 |

langgraph-claw 的真实实现（`skills/loader.py`）在此基础上还增加了：
- **script_tools**: frontmatter 中声明的 `scripts` 块自动编译为 LangChain Tool
- **hot-plug**: 通过 `watchfiles` 监控技能目录，自动热重载
- **hash 校验**: 用 SKILL.md 的 mtime + SHA1 检测变更，避免重复加载

### 4. 与 s01/s02 的集成

从本章开始，agent graph 多了一个隐式的"加载技能"语义——不是新增节点，而是
通过以下方式集成：

1. **SystemMessage 注入**: `build_system_message()` 读取 registry 的技能摘要，
   追加到 LLM 的系统提示中
2. **load_skill 工具**: 注册为 LangChain Tool，LLM 可以主动调用它来获取技能完整指令
3. **Agent 不变**: StateGraph 结构（agent → tools loop）完全没有改动

这正是 LangGraph harness 的设计哲学：**Graph 结构保持简单，功能通过节点内部逻辑
和工具扩展**。

## 变更内容

| 组件 | s02 | s03 |
|------|-----|-----|
| 技能系统 | （无） | `SkillRegistry` + 两阶段加载 |
| SKILL.md 格式 | （无） | YAML frontmatter + Markdown body |
| 工具 | bash, read_file, write_file | + `load_skill`（加载技能指令） |
| SystemMessage | 静态文本 | 动态注入技能摘要 |
| 技能目录 | （无） | `skills/weather/SKILL.md` |
| 依赖 | langgraph, langchain, dotenv | + `pyyaml`（frontmatter 解析） |

## 试一试

```sh
cd course
pip install pyyaml   # 如果还没有
python s03_skill_loading/code.py
```

试试这些 prompt：

1. `北京今天天气怎么样？` —— LLM 看到 weather 技能摘要，调用 `load_skill("weather")`
   获取完整指令，然后执行天气查询
2. `有什么技能可用？` —— SystemMessage 中已注入技能列表，LLM 可以直接回答
3. `帮我查一下上海的天气` —— 验证触发器"天气"能匹配
4. `明天会下雨吗` —— 验证触发器"下雨"能匹配

观察输出：第一次调用 weather 技能时，`load_skill` 会从磁盘读取 SKILL.md 正文。
第二次不会再读（Phase 2 只读一次）。

## 下一步

[s04: Skill Routing](../s04_skill_routing/) —— 从手动 `load_skill` 到自动路由：
如何用正则触发词 + 语义搜索在 50+ 技能中精准定位最合适的技能。
langgraph-claw 的真实实现用 `router.py` 做多层匹配：精确触发 → 模糊触发 → 语义 fallback。
