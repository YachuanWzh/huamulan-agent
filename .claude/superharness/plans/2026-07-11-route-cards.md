# Plan — 路由中间结果不再吐 JSON，用卡片承接

## 目标（一句话）
把 `route_skills`（及多 agent 的 `rewrite_intent`）节点内部"查询改写 JSON"和"技能路由 JSON"从可见 token 流里移除，改为服务端推送带类型标识的 `card` SSE 事件，前端渲染成两张独立可折叠卡片。

## 根因
- `harness.py` 的 `on_chat_model_stream` 处理里，`token` 分支没有节点白/黑名单，`route_skills` 内部两次 LLM 调用（`query_rewriter.rewrite` + 技能路由 `llm.ainvoke`）的流式 JSON 被当正文 token 转发。
- 前端 `useChat.ts` 把非 child 的 token 一律 append 进主气泡。

## 契约
服务端新增 SSE 事件：`event: card` + `data`：
- `{card_type:"query_rewrite", rewritten_query, original_query, intent, secondary_intents, confidence, needs_clarification, missing_slots, sub_queries}`
- `{card_type:"skill_route", selected_skills, confidence, reason, stage}`

SSE 解析器把 `event: card` 变成 `{type:'card', card_type, ...}`。卡片在 `route_skills` 节点 `on_chain_end` 时发出（早于正文 token）。

## 假设
- 在 `master` 原地开发（工作区已有未提交的 query-rewrite 特性，本任务依赖它，worktree 从 HEAD 拉会丢）。
- 黑名单（silent nodes）而非白名单：只静默 `route_skills`/`rewrite_intent`，避免误伤 `agent`/`synthesize` 以及 `node:""` 的既有测试。

## 任务（每个 TDD：RED→GREEN→commit）
1. **后端·静默路由节点 token**：`_SILENT_TOKEN_NODES={"route_skills","rewrite_intent"}`；silent 节点的 content/reasoning/tool_call 不再发。测试：带 `metadata.langgraph_node="route_skills"` 的 chunk 不产生 `token`；`node:""` 与 `agent` 仍产生 `token`（保留既有断言）。
2. **后端·发 card 事件**：`_route_card_events(event)` 在 `on_chain_end` 且 `name in {route_skills,rewrite_intent}` 时，从 `data.output` 构造两张卡。测试：给定 route_skills 的 output（含 intent_slots + selected_skills + routing_trace）产生两个 `event: card`；intent_slots 为空时只产生 skill_route 卡。
3. **前端·api.ts 类型**：`StreamQueryRewriteCard`/`StreamSkillRouteCard`（`type:'card'`）加入 `StreamEvent` 联合。测试：`api.test.ts` 解析 `event: card` → `{type:'card', card_type:...}`。
4. **前端·useChat**：`case 'card'` 把卡片挂到当前 assistant 消息（`message.cards`）。测试：喂入 card 事件后 `messages` 里对应消息带 cards。
5. **前端·MessageBubble**：渲染两张可折叠卡片（复用现有卡片样式）。测试：给带 cards 的 message，渲染出改写卡与路由卡关键字段。
6. **验证与收尾**：后端 pytest 全量 + 前端 vitest 全量；code review；报告。

## 验证命令
- 后端：`cd backend && python -m pytest tests/test_stream_error_handling.py tests/test_route_cards.py -q` 然后全量 `python -m pytest -q`
- 前端：`cd frontend && npx vitest run src/lib/api.test.ts src/hooks/useChat.test.ts src/components/MessageBubble.test.tsx` 然后全量 `npx vitest run`
