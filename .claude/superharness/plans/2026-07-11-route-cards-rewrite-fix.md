# Plan — 修复查询改写完全照搬输入的 bug

## 背景
用户截图显示：「查询改写」卡片中"改写后"和"原始"完全相同，置信度 70%（regex 计算值）。
诊断为两个并存问题：
1. **regex fallback 路径**：当 LLM 返回 confidence < 0.60 时，fallback 到 `rewrite_query_fast`，
   该函数只做 `" ".join(query.split())`，对中文查询几乎等价于原文。
2. **LLM 高置信度但保持原文**：LLM 可能返回 `rewritten == original` 但 `confidence < 1.0`，
   这违反了 prompt 中"无需改写时 confidence=1.0"的规则，但仍被接受。

## 根因（代码层）
- `backend/src/personal_assistant/agent/query_rewriter.py:540` — fallback 使用 `fast.rewritten`
  等价于"仅空白归一化"，对中文输入无效。
- `query_rewriter.py:333` — `_parse_rewrite_llm_response` 不检查 `rewritten==original ∧ conf<1.0` 矛盾态。
- `QUERY_REWRITE_PROMPT` — 例子 5 鼓励 LLM 在"查询已清晰"时返回 rewritten=original，
  但缺少"这种情况下 confidence 必须=1.0"的硬约束。

## 目标（最小修复）
让用户在任何路径下都能看到有意义、或明确被告知"未改写"。
- LLM 路径：若 LLM 返回 rewritten=original 但 confidence<1.0 → 视为矛盾，降级到 regex + 强制归一化。
- regex 路径：增加基础中文口语化归一化（"我想看看"→"查看"、"怎么样"→"情况" 等）。
- 卡片显示：当前"改写后"和"原始"重复时，整行隐藏原始行（已有逻辑）；补强"未改写"的语义传达。

## 任务（TDD：每个 RED→GREEN→commit）

### Task 1 — fallback 路径产出可见改写（中文口语归一化）
- RED：在 `tests/test_query_rewriter.py` 加 `test_rewriter_fallback_normalizes_colloquial_chinese`。
  输入"我想看看这个系统的运行状况，它的指标怎么样"，Mock LLM 返回 conf=0.50，
  断言 `result.rewritten != result.original`，且包含"查看"或"查询"。
- GREEN：扩 `rewrite_query_fast`，加入口语化 → 标准化 的轻量映射表。
  （不引入新依赖；regex 规则覆盖常见 5-8 个模式即可。）

### Task 2 — LLM 矛盾态降级（rewritten==original ∧ conf<1.0 → fallback）
- RED：`test_rewriter_demotes_llm_identity_with_low_confidence`。
  Mock LLM 返回 `{"rewritten": <original>, "confidence": 0.70, ...}`，
  断言 `result.rewritten != result.original`（被 regex 接管）或 `result.confidence == 1.0`。
- GREEN：在 `_parse_rewrite_llm_response` 返回前/调用方加入 guard。
  干净做法：在 `QueryRewriter.rewrite` 中，若 `decision.rewritten.strip() == user_query.strip()`
  且 `decision.confidence < 0.99`，走 fallback 路径。

### Task 3 — prompt 加硬约束
- 改 `QUERY_REWRITE_PROMPT`：在"无需改写"段加"**confidence 必须=1.0**；否则视为改写未完成"。
- 不写新测试（这是 prompt 文案，靠人工评估）。

### Task 4 — 前端卡片补强
- 若 `card.rewritten_query == card.original_query`，把"改写后"那一行标题改为
  "改写后（无变化）"或在末尾追加"未做语义改写"小字。
- RED：在 `MessageBubble.test.tsx` 加测试覆盖此分支。
- GREEN：MessageBubble.tsx 小改。

### Task 5 — 验证
- `pytest tests/test_query_rewriter.py tests/test_route_cards.py -q` 然后全量 `pytest -q`
- 前端 `npx vitest run src/components/MessageBubble.test.tsx`
- code review

## 验证命令
- 后端：`cd backend && python -m pytest tests/test_query_rewriter.py tests/test_route_cards.py -q`
  然后 `python -m pytest -q`
- 前端：`cd frontend && npx vitest run src/components/MessageBubble.test.tsx`
  然后 `npx vitest run`