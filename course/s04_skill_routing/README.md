# s04: Skill Routing（技能路由）

`[ s04 ] s01 > s02 > s03 > s04 > s05 > s06 > s07 > s08 > s09 > s10 > s11 > s12 > s13 > s14 > s15 > s16`

> *"不是 agent 调用技能，是 agent 背上了技能。"* —— 路由节点在 LLM 推理之前完成技能的
> 选择和激活。
>
> **Harness 层**: 路由 —— 按需装载上下文，而不是让 LLM 看过所有的东西。

## 问题

s03 实现了技能注册中心——能从 YAML frontmatter 中读到技能元数据和指令。但这引出了
一个新问题：**什么时候激活哪个技能？**

最简单的方案是把所有已安装技能的指令全部塞进 system prompt 里，让 LLM 自己判断。
但这是 O(n) 的上下文浪费——每次推理都注入几十个技能的完整指令，大部分跟当前任务
毫无关系。上下文窗口很快被吃光，token 成本飙升。

更糟糕的是，LLM 并不擅长在大量背景噪声中筛选正确的工具。它可能选了不相关的技能，
也可能漏掉真正需要的技能。

你需要一个**在 LLM 推理之前**就完成筛选的机制——轻量、确定、透明。

## 解决方案

在 StateGraph 里，**agent_node 之前**插入一个 `route_skills` 节点：

```
+--------+      +----------------+      +-------------+      +---------+
|  User  | ---> | route_skills   | ---> | agent_node  | ---> |  tools  |
| prompt |      | (regex select) |      | (LLM call)  |      | (bash)  |
+--------+      +----------------+      +------+------+      +----+----+
                                               ^                  |
                                               |   tool_result    |
                                               +------------------+
                                          (loop via conditional edge)
```

**路由节点不调用技能**——它只做一件事：决定哪些技能应该激活。选中后，这些技能的
指令被注入 system prompt，LLM 就能看到并使用它们。技能本身仍然是 LLM 通过工具调用
来驱动的，路由只是决定了"哪些工具可用"。

## 工作原理

### 1. AgentState 增加一个字段

```python
class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    selected_skills: list[str]  # 新增：本轮选中的技能名列表
```

### 2. route_skills 节点：regex 触发匹配

每个技能声明一组 regex 模式作为"触发词"。当用户消息匹配某个技能的任一 regex 时，
该技能被选中：

```python
def route_skills(state: AgentState) -> dict:
    user_text = "\n".join(
        msg.content for msg in state["messages"]
        if getattr(msg, "type", "") == "human"
    )[-4000:]  # 只看最近 4000 字符

    selected = []
    for skill in SKILLS.values():
        for pattern in skill.triggers:
            if re.search(pattern, user_text, re.IGNORECASE):
                selected.append(skill.name)
                break  # 一个技能匹配一次就够了
    return {"selected_skills": selected}
```

### 3. agent_node 注入选中技能的指令

```python
def agent_node(state: AgentState) -> dict:
    # 基础 system prompt
    parts = [BASE_PROMPT]

    # 注入选中技能的指令
    for name in state["selected_skills"]:
        skill = SKILLS[name]
        parts.append(f"## Skill: {skill.name}\n{skill.instructions}")

    system = SystemMessage(content="\n\n".join(parts))
    messages = [system] + state["messages"]
    response = LLM_WITH_TOOLS.invoke(messages)
    return {"messages": [response]}
```

### 4. 三层漏斗：只有 regex 在 code.py 中实现

真实项目（`agent/router.py`）中的路由是三层漏斗，按成本和复杂度递增：

```
regex（确定性，零成本）
    ↓ 无匹配时
semantic search（embedding，需要向量模型）
    ↓ 候选分低于阈值时
LLM judge（最昂贵，最后手段）
```

| 层级 | 机制 | 成本 | 何时使用 |
|------|------|------|---------|
| Layer 1: Regex | 确定性正则匹配 | 零（CPU） | 永远运行，首个匹配即生效 |
| Layer 2: Semantic | embedding 余弦相似度 | 低（向量模型一次调用） | regex 未命中时 |
| Layer 3: LLM judge | 让 LLM 从候选里选一个 | 高（一次 LLM 调用） | semantic 不确定时 |

**code.py 只实现 Layer 1** —— 这是最核心、最无争议的一层。regex 匹配是确定性的：
同样的输入永远得到同样的结果，没有概率、没有阈值调参、没有幻觉风险。对于绝大多数
技能（天气、时间、文件操作、股票查询），regex 足以覆盖日常触发场景。

Layer 2 和 3 是可选增强，在 README 中说明原理即可。

### 5. 对比：router 是"选择"不是"调用"

| | 技能调用（Skill Invocation） | 技能路由（Skill Routing） |
|---|---|---|
| 谁做决策 | LLM（通过 tool_choice） | harness（regex/embedding） |
| 时机 | 推理时，作为 tool_call 返回 | 推理前，路由节点单独运行 |
| 粒度 | LLM 决定"用哪个工具" | harness 决定"哪些工具可用" |
| 对 LLM 的影响 | 收到 tool_result 再继续推理 | 看不到未选中的技能指令，节省上下文 |
| 可观测性 | 在消息流中查找 tool_call | 直接看 selected_skills 字段 |

**路由缩小了 LLM 的选项空间**。这不是限制 LLM 的能力，而是减少它的认知负担。
LLM 只需要在 3 个相关技能里做选择，而不是在 30 个无关技能里大海捞针。

### 6. regex 触发词的最佳实践

从 `_DEFAULT_SKILL_REGEXES` 中总结的设计原则：

- **具体但不狭窄**：`\bweather\b` 太窄？加上 `forecast`, `temperature`, `rain` 等
- **中英双语覆盖**：真实项目里每个技能都有中英文 regex
- **匹配用户意图，不是匹配技能名**：`resolve-time` 的 regex 是 `today|tomorrow|date`
  而不是 `resolve-time|time resolution`
- **避免过度匹配**：用 `\b` 边界，不要匹配子串（`cat` 不要匹配 `category`）
- **每个技能 8-12 个 regex 为宜**：太少可能漏配，太多说明技能职责不清

## 变更内容

| 组件 | 之前（s01/s03） | 之后（s04） |
|------|----------------|------------|
| AgentState | `messages` 一个字段 | + `selected_skills` |
| Graph 节点 | `agent → tools → agent` | `route_skills → agent → tools → agent` |
| 技能激活 | 无机制 / 手动 | 正则自动匹配 |
| System Prompt | 固定文本 | 基础提示 + 动态注入选中技能指令 |
| 技能定义 | `name + instructions` | `name + instructions + triggers (regex)` |

## 试一试

```sh
cd course
python s04_skill_routing/code.py
```

试试这些 prompt，观察哪些技能被选中：

| Prompt | 期望选中的技能 | 原因 |
|--------|--------------|------|
| `今天天气怎么样？` | weather | 匹配 `天气` regex |
| `现在几点了？` | datetime | 匹配 `几点` regex |
| `帮我找一下 README.md 在哪里` | file-search | 匹配 `找` + 文件名模式 |
| `天气很热，什么时候下雨？` | weather, datetime | 匹配 `天气` + `什么时候` |
| `hello` | （无） | 没有任何 regex 匹配 |
| `帮我创建一个 hello.py` | （无） | 创建不是搜索，不触发 file-search |

注意最后一个例子：`创建` 不触发 `file-search`，因为 file-search 的 regex 是
`find\|search\|找`。这是**精确路由**的体现——不会过度激活不相关的技能。

## 下一步

[s05: Script Tools](../s05_script_tools/) —— 技能不只是静态指令，还能带上可执行脚本。
脚本声明放在 YAML frontmatter 里，运行时动态生成 LangChain Tool。这是技能系统从
"提示词注入"到"能力注入"的质变。

---

**源码参考**: `backend/src/personal_assistant/agent/router.py`（569 行）
`build_skill_router` / `route_skill_names` / `_regex_route`
