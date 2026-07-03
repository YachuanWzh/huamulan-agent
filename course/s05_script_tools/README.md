# s05: Script Tools（脚本工具）

`[ s03 > s04 > s05 ] s06 > s07 > s08 > s09 > s10 > s11 > s12 > s13 > s14 > s15 > s16`

> *"声明在 YAML，实现在 Python，绑定在运行时"* —— 让 skill 不再是纯文本，
> 而是自带工具的完整能力包。
>
> **Harness 层**: 动态工具生成 —— skill 可以带上自己的工具。

## 问题

s03 和 s04 解决了"何时用哪个 skill"的问题，但每个 skill 只能注入一段文本到
system prompt。真正的 skill 不应该只是提示词——它应该是"文档 + 触发器 + 工具"
的完整包。比如天气查询 skill，光告诉模型"你可以查天气"是不够的——你得给它
一个真正能查天气的函数。

langgraph-claw 的现实需求是：每个 skill 的 YAML frontmatter 里可以声明
`scripts` 字段，描述这个 skill 提供了哪些可调用的工具。resolve 时，harness
动态加载对应的 Python 实现，包装为 LangChain Tool，绑定到 LLM。

## 解决方案

```
+------------------+     resolve      +-------------------+
| SKILL.md         | ---------------> | LangChain Tools   |
| frontmatter:     |     (动态导入)    |                   |
|   scripts:       |                  | get_current_      |
|   - name: ...    |                  |   weather(city)   |
|   - description  |      +           | get_forecast(     |
|   - parameters   |    加载           |   city)           |
+------------------+                  +-------------------+
         |                                    |
         v                                    v
+------------------+                  +-------------------+
| skill.py         |                  | LLM.bind_tools()  |
| def get_current_ | ---------------> | agent loop        |
|   weather(city)  |  包装为 Tool      |                   |
| def get_forecast |                  | (s01 StateGraph)  |
+------------------+                  +-------------------+
```

三段式模式：

1. **声明层（SKILL.md YAML frontmatter）**—— 描述工具叫什么、做什么、接受什么参数。
   LLM 通过 tool definition 知道这个工具的存在和用法。

2. **实现层（skill.py）**—— 包含 Python 函数，有类型标注和 docstring。
   可以独立运行和测试，不依赖 agent 框架。

3. **绑定层（resolve 时）**—— harness 解析 frontmatter，动态导入 skill.py，
   把函数包装为 `StructuredTool`，加入工具列表，`bind_tools` 到 LLM。

### 对比 s03/s04：静态文本 vs 动态工具

| | s03/s04（Skill 路由） | s05（Script Tools） |
|---|---|---|
| Skill 提供的内容 | 文本（注入 system prompt） | 文本 + 工具函数 |
| 工具生成方式 | 无（工具是固定的 bash/read/write） | 动态：每个 skill 可带自己的工具 |
| 扩展方式 | 写一段提示词 | 写一段提示词 + 一个 Python 函数 |
| Harness 的职责 | 路由 skill → 注入文本 | 解析声明 → 导入函数 → 包装为 Tool |
| 典型场景 | 告诉模型"如何处理日期" | 给模型一个真正能查日期的函数 |

**核心洞察**：s03/s04 解决的是"知识"问题（skill 告诉模型怎么做），s05 解决的
是"能力"问题（skill 给模型真正能执行的工具）。两者组合，skill 才完整。

## 工作原理

### 1. SKILL.md 中的 scripts 声明

```yaml
---
name: weather
description: 天气查询
triggers:
  - 天气
  - 气温
scripts:
  - name: get_current_weather       # 工具名称（对应 Python 函数名）
    description: Query current weather for a city
    parameters:
      city:
        type: string
        description: City name
        required: true
  - name: get_forecast
    description: Query 3-day forecast
    parameters:
      city:
        type: string
        description: City name
        required: true
---
```

每个 `scripts` 条目是一个工具声明。`parameters` 是 JSON Schema —— LLM 通过它
知道应该传什么参数。`parameters` 在 course 代码中作为文档/元数据存在；实际
schema 由 Pydantic 从 Python 函数的类型标注自动推断。

### 2. create_script_tool 工厂

```python
def create_script_tool(name, description, func, parameters=None):
    """Wrap a Python function as a LangChain StructuredTool."""
    return StructuredTool.from_function(
        func=func,
        name=name,
        description=description,
    )
```

`StructuredTool.from_function` 自动从函数的类型标注构建 Pydantic args_schema。
例如 `def get_current_weather(city: str) -> str` 会自动生成一个接受 `city: str`
参数的 schema。

真实 project 中的 `build_script_tool()`（`skills/script_tool.py`）更复杂：
- 从 params 声明手动构建 Pydantic model（`create_model` + `Field`）
- 支持 type mapping（`integer` -> `int`, `string` -> `str` 等）
- 支持子进程执行（`subprocess.run`）而非直接调用 Python 函数
- 支持 async wrapper（`asyncio.to_thread`）

Course 代码做了简化：直接调用 Python 函数，让 Pydantic 自动推断 schema。

### 3. resolve 流程

```
For each skill directory under skills/:
  1. 存在 SKILL.md？→ 解析 YAML frontmatter
  2. 有 scripts 声明？→ 继续
  3. 存在 skill.py？→ importlib 动态导入
  4. For each script declaration:
     a. 从 module 中取出同名函数
     b. 调用 create_script_tool() 包装
     c. 加入工具列表
  5. 所有工具收集完毕后 → LLM.bind_tools(all_tools)
```

动态导入使用 `importlib.util.spec_from_file_location`——它不要求 skill.py 在
PYTHONPATH 中，可以从任意路径加载。

### 4. 与真实实现的差异

| 方面 | Course 代码 (s05) | 真实项目 (script_tool.py) |
|------|------------------|-------------------------|
| 执行方式 | 直接调用 Python 函数 | 子进程 `subprocess.run` |
| Schema 构建 | Pydantic 自动推断 | 手动 `create_model` + Field |
| 工具类型 | `StructuredTool.from_function` | 同，参数更多 |
| command 参数 | 无（直接调函数） | 有（`command: ["python", "scripts/x.py", "{arg}"]`） |
| 异步支持 | 无（简化） | 有（`asyncio.to_thread`） |

真实项目用子进程的原因：脚本可以用**任何语言**写（Python, Bash, Node.js...），
只要遵守 `argv in → stdout out` 契约。Course 限制为 Python 函数是为了减少
概念噪音，聚焦在"声明 → 解析 → 绑定"的核心流程。

## 变更内容

| 组件 | 之前 (s04) | 之后 (s05) |
|------|-----------|-----------|
| Skill 的能力 | 仅文本注入 | 文本 + 工具函数 |
| 工具列表 | 固定的基础工具 | 基础工具 + 动态加载的 script tools |
| Skill 目录 | SKILL.md | SKILL.md + skill.py |
| Resolve 步骤 | 解析 YAML → 读 body | 解析 YAML → 读 body → 导入 skill.py → 组装工具 |
| LLM 看到的 | system prompt 里有 skill 文本 | system prompt + tool definitions |

## 试一试

```sh
cd course
pip install langgraph langchain-core langchain-openai python-dotenv pyyaml
OPENAI_API_KEY=... python s05_script_tools/code.py
```

试试这些 prompt：

1. `北京今天天气怎么样`
2. `上海这周天气预报`
3. `纽约会下雨吗`
4. `深圳现在的温度和湿度`

也可直接调用 skill 函数来测试：

```sh
cd course/s05_script_tools
python -c "from skills.weather.skill import get_current_weather; print(get_current_weather('北京'))"
```

## 源码位置

| 文件 | 说明 |
|------|------|
| `code.py` | 独立可运行的课程实现 |
| `skills/weather/SKILL.md` | 天气 skill 的 YAML 声明 |
| `skills/weather/skill.py` | 天气 skill 的函数实现 |
| `backend/src/personal_assistant/skills/script_tool.py` | 真实项目源码（子进程版） |
| `backend/src/personal_assistant/skills/weather/SKILL.md` | 真实天气 skill 声明 |

## 下一步

[s06: Approval Gate](../s06_approval_gate/) —— 工具审批管线：不是所有工具调用
都应该直接执行。用户可能需要审批。在 `tools` 节点前插入 `approval` 节点，
实现"暂停 → 等待用户决策 → 执行或拒绝"的安全模式。
