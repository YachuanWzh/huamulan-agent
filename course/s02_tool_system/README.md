# s02: Tool System（工具体系）

`[ s01 ] s02 > s03 > s04 > s05 > s06 > s07 > s08 > s09 > s10 > s11 > s12 > s13 > s14 > s15 > s16`

> *"ToolNode is the universal dispatcher that replaces handwritten if/elif chains"* —— 定义一个工具函数，注册到列表，ToolNode 接管全部调度：解析 tool_call、按名匹配、校验参数、执行函数、打包结果。你不写一行 dispatch 代码。
>
> **Harness 层**: 工具系统 —— LLM 与真实世界的多通道连接。

## 问题

s01 的 agent 只有一个 `bash` 工具——能跑命令，但不够精确：

- LLM 用 `bash` 读文件需要拼接 `cat path/to/file`，容易引入命令注入和转义问题
- LLM 用 `bash` 写文件需要拼接 `echo "..." > file`，内容的引号和换行极易出错
- LLM 用 `bash` 列目录需要拼接 `ls`，输出格式不可控

**真实世界需要专用工具**——每个工具做一件事，参数精确，返回值结构化。但问题是：加工具的同时，手写 dispatch 逻辑会膨胀。

learn-claude-code 采用手动分发：

```javascript
// learn-claude-code 风格：手写 tool dispatch
function executeTool(toolName, toolInput) {
    if (toolName === "bash") return runBash(toolInput.command);
    else if (toolName === "read") return read(toolInput.file_path);
    else if (toolName === "write") return write(toolInput.file_path, toolInput.content);
    // ... 每加一个工具，这里多一行
}
```

langgraph-claw 用 **LangGraph `ToolNode`** 消除所有手动 dispatch——加工具就是加一个 `@tool` 函数，然后把它丢进列表。

## 解决方案

```
               +-----------------------------+
               |         ToolNode            |
               |  (LangGraph 内置调度器)      |
               +-----------------------------+
               |  bash         | command -> exec
               |  read_file    | path -> read
               |  write_file   | path+content -> write
               |  list_directory| path -> ls
               +--+--+--+--+--+--+--+--+-----+
                  ^  ^  ^  ^
                  |  |  |  |
    +--------+    |  |  |  |  tool_calls = [
    | agent  | ---+--+--+--+  (read_file, "/tmp/x"),
    | (LLM)  | ---+--+--+--+  (write_file, "y.py", "..." ),
    +--------+    |  |  |     (bash, "python y.py") ]
                  |  |
       ToolNode 自动:
       1. 解析 tool_call.name
       2. 按 name 匹配工具函数
       3. 校验 arguments 是否符合 input_schema
       4. 执行函数
       5. 返回 ToolMessage
```

关键在于：**Graph 结构不变**。s01 的 `agent → tools → agent` 循环完全保留，变的只是 TOOLS 列表——从 `[bash]` 变成 `[bash, read_file, write_file, list_directory]`。

## 工作原理

### 1. `@tool` 装饰器：从 Python 函数到 LLM 可调用的工具

```python
from langchain_core.tools import tool

@tool
def read_file(path: str) -> str:
    """Read a UTF-8 text file from the workspace."""
    target = WORKSPACE / path
    return target.read_text(encoding="utf-8")
```

`@tool` 自动从函数签名提取三层关键信息：

| 内容 | 来源 | LLM 看到的效果 |
|------|------|---------------|
| `name` | 函数名 `read_file` | 工具名称，用于匹配 |
| `description` | docstring | LLM 据此判断何时调用这个工具 |
| `input_schema` | 类型注解 `path: str` | LLM 知道要传什么参数、什么类型 |

不需要手动写 JSON Schema。函数签名即 Schema。

对标真实项目 `backend/src/personal_assistant/tools/basic.py` 中的工具定义，核心模式完全一致——区别仅在于真实版本多了 workspace 沙箱校验和安全隔离。

### 2. 工具注册：一个列表

```python
# s01: 1 个工具
TOOLS = [bash]

# s02: 4 个工具——只改这一行
TOOLS = [bash, read_file, write_file, list_directory]
```

这就是整个扩展。不加 dispatch 逻辑，不改 graph 结构，不改 router。

### 3. ToolNode：LangGraph 内置的工具执行器

```python
from langgraph.prebuilt import ToolNode

TOOL_NODE = ToolNode(TOOLS)
```

`ToolNode(TOOLS)` 在每轮工具调用时做的事：

1. 拿到 `AIMessage` 中的 `tool_calls`
2. 遍历每个 `tool_call`：
   - `tool_call["name"]` 匹配 TOOLS 列表中同名的工具
   - `tool_call["args"]` 传入函数作为关键字参数
   - 捕获函数返回值，封装为 `ToolMessage`
3. 返回 `{"messages": [ToolMessage, ...]}`


> **与 learn-claude-code 的核心区别**：ToolNode 用名字做自动分发。learn-claude-code 用 `if toolName === "bash"` 手动匹配。前者是声明式注册，后者是命令式分支。声明式注册意味着加工具是加数据（列表追加），不加逻辑（不改控制流）。

### 4. 四个工具的完整定义

```python
@tool
def bash(command: str) -> str:
    """Run a shell command in the workspace."""
    r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
    return (r.stdout + r.stderr).strip() or "(no output)"

@tool
def read_file(path: str) -> str:
    """Read a UTF-8 text file from the workspace."""
    target = WORKSPACE / path
    return target.read_text(encoding="utf-8", errors="replace") if target.is_file() else f"Not found: {path}"

@tool
def write_file(path: str, content: str) -> str:
    """Write UTF-8 text to a file in the workspace."""
    target = WORKSPACE / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content.encode('utf-8'))} bytes to {path}"

@tool
def list_directory(path: str = ".") -> str:
    """List direct children of a directory."""
    target = WORKSPACE / path
    return "\n".join(f"{c.name}{'/' if c.is_dir() else ''}" for c in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())))
```

### 5. `bind_tools`：工具能力注入 LLM

```python
LLM_WITH_TOOLS = ChatOpenAI(...).bind_tools(TOOLS)
```

`bind_tools(TOOLS)` 的工作：

- 遍历 TOOLS 列表中的每个工具
- 提取其 `name`、`description`、`input_schema`
- 按 OpenAI function calling 格式注入请求
- LLM 返回 `AIMessage` 时，`tool_calls` 已包含结构化的工具调用请求

注意：`bind_tools` 是 **注入能力**，不是 **注入指令**。LLM 自己决定是否调工具、调哪个、传什么参数。

### 6. Graph 完全不变

```python
graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", TOOL_NODE)          # ToolNode 自动处理所有工具
graph.set_entry_point("agent")
graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")
app = graph.compile()
```

s01 和 s02 的 graph 构建代码完全相同。唯一区别：`TOOLS = [bash]` vs `TOOLS = [bash, read_file, write_file, list_directory]`。

这就是 LangGraph 的声明式优势：**架构 = Graph 结构，能力 = 工具列表**。两者解耦。扩展能力不改架构。

## 变更内容

| 组件 | s01 | s02 |
|------|-----|-----|
| 工具数量 | 1（bash） | 4（bash, read_file, write_file, list_directory） |
| 工具定义方式 | `@tool` | `@tool`（不变） |
| 工具注册 | `TOOLS = [bash]` | `TOOLS = [bash, read_file, write_file, list_directory]` |
| 工具调用 | bash 拼接 cat/echo/ls | 专用工具：精确参数、结构化返回 |
| 分发逻辑 | ToolNode 1 对 1 | ToolNode 1 对 多（自动按名匹配） |
| Graph 结构 | `agent → tools → agent` | `agent → tools → agent`（不变） |
| bind_tools | `bind_tools([bash])` | `bind_tools(TOOLS)`（不变模式） |
| LLM 效率 | 每次文件操作都要起子进程 | 文件操作零子进程开销 |

## 试一试

```sh
cd course
python s02_tool_system/code.py
```

试试这些 prompt——对比 s01 只能用 bash 时的差异：

1. `Create a Python file called hello.py that prints "Hello, World!" and verify it works`
   - s01: bash 拼接 `echo > file` + `python file.py`
   - s02: `write_file` + `bash python hello.py`

2. `Read the content of hello.py`
   - s01: `bash cat hello.py`（LLM 手动拼接命令）
   - s02: `read_file("hello.py")`（专用工具，零子进程）

3. `Create a directory called my_project with 3 files: main.py, utils.py, and README.txt`
   - s01: `bash mkdir ... && echo ... > ...`（易出错）
   - s02: `bash mkdir` + 3 次 `write_file`

4. `List all files in the current directory, show me which are directories`
   - s01: `bash ls -la`（人类可读但程序解析弱）
   - s02: `list_directory(".")`（结构化输出，目录以 `/` 结尾）

观察 LLM 的行为变化：有了专用工具后，LLM 更倾向于选择精确的工具而非万能的 bash。

## 工具系统的设计原则

从 `basic.py` 的真实代码和本章简化版中可以提炼出三个原则：

1. **一个工具做一件事**：`read_file` 只读文件，不混入搜索功能。LLM 更容易选择正确的工具。
2. **返回值结构化**：成功/失败用明确的字符串模板（如 `wrote N bytes to path`），帮助 LLM 正确理解执行结果。
3. **工具即文档**：每个工具的 docstring 替代了 prompt 中的工具使用说明。加工具自动加能力，无需改 system prompt。

## 下一步

[s03: Skill Loading](../s03_skill_loading/) —— 从内置工具扩展到外部可插拔技能：YAML frontmatter 定义技能元数据，SkillRegistry 动态加载，让 agent 的能力边界由文件系统而非代码决定。
