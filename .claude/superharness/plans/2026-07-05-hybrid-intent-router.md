# Multi-Agent Hybrid 三层意图识别 实现计划

> **For agentic workers:** Execute this plan task-by-task under the superharness:go
> workflow, Phase 2 (strict TDD per task). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Multi-agent 模式下复用 Single-agent 的三层漏斗基建（embedding provider、reranker、LLM judge），实现 Hybrid 意图路由：正则 → 语义 → LLM，不影响现有 single-agent 路由逻辑。

**Architecture:** 新建 `intent_router.py` 模块，复用 `router.py` 中的 `OllamaBgeM3EmbeddingProvider` 和 `OllamaBgeM3Reranker`。为每个意图类别定义示例语句（`INTENT_UTTERANCES`），embedding 后存入 `IntentEmbeddingIndex`（内存版，Qdrant 版后续扩展）。`multi_agent.py` 的 `rewrite_intent` 节点改为调用新的三层漏斗 `route_intent_with_trace()`。

**Tech Stack:** Python 3.12+, Pydantic, LangGraph AgentState, Ollama BGE-M3

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `backend/src/personal_assistant/agent/intent_router.py` | 新建 | 意图示例语句、IntentEmbeddingIndex、route_intent_with_trace()、LLM 分类器 |
| `backend/src/personal_assistant/agent/multi_agent.py` | 修改 | rewrite_intent 节点接入三层漏斗 |
| `backend/src/personal_assistant/config.py` | 修改 | 新增 MULTI_AGENT_INTENT_* 配置项 |
| `backend/tests/test_intent_router.py` | 新建 | 意图路由全套测试 |

---

### Task 1: 定义 Intent Slots Schema 和意图示例语句

**Files:**
- Create: `backend/src/personal_assistant/agent/intent_router.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intent_router.py
from personal_assistant.agent.intent_router import (
    INTENT_UTTERANCES,
    IntentSlots,
    IntentCandidate,
)


def test_intent_utterances_covers_all_intents():
    """每个意图类别都有足够的示例语句"""
    for intent in ("troubleshoot", "patrol", "audit", "metrics"):
        assert intent in INTENT_UTTERANCES
        assert len(INTENT_UTTERANCES[intent]) >= 6, f"{intent} needs >=6 utterances"


def test_intent_slots_defaults():
    """IntentSlots 默认值检查"""
    slots = IntentSlots()
    assert slots.domain == "general"
    assert slots.primary_intent == "general"
    assert slots.secondary_intents == []
    assert slots.confidence == 0.0
    assert slots.source == "regex"
    assert slots.metrics == []
    assert slots.entities == []


def test_intent_candidate_fields():
    """IntentCandidate 必须有 name, score, description"""
    c = IntentCandidate(name="troubleshoot", score=0.85, description="排障")
    assert c.name == "troubleshoot"
    assert c.score == 0.85
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_intent_router.py -v
```
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/personal_assistant/agent/intent_router.py
"""Multi-agent intent routing with a 3-tier funnel (regex → semantic → LLM).

Reuses the single-agent embedding infrastructure (OllamaBgeM3EmbeddingProvider,
OllamaBgeM3Reranker) from router.py.
"""
from dataclasses import dataclass, field
from typing import Any


# ── Intent utterances (Tier 1 semantic matching) ──────────────────────
# Each intent category has example user queries. These are embedded and
# stored in the vector index. At query time the user's query is embedded
# and compared against the centroid of each intent's utterance embeddings.

INTENT_UTTERANCES: dict[str, list[str]] = {
    "troubleshoot": [
        "排查 payment-service 超时问题",
        "帮我做一下根因分析",
        "最近错误率升高了是什么原因",
        "RCA the latency spike on api-gateway",
        "为什么数据库查询突然变慢了",
        "服务挂了帮我看看",
        "frontend error rate is spiking, need root cause",
        "分析一下 APM 里面的异常 trace",
    ],
    "patrol": [
        "设置一个夜间自动巡检规则",
        "配置告警阈值",
        "创建健康检查任务",
        "定时巡检所有服务",
        "帮我配一条 p99 > 500ms 的告警",
        "add a patrol rule for error_rate > 5%",
        "每日凌晨自动巡检生产环境",
        "配置核心接口的可用性监控",
    ],
    "audit": [
        "审计一下最近的工具调用记录",
        "查看执行日志",
        "检查 SLA 合规情况",
        "审批通过率是多少",
        "audit the tool execution logs for security events",
        "跨线程治理巡检",
        "查看最近一周的操作审计日志",
        "合规性检查：谁修改了告警规则",
    ],
    "metrics": [
        "LCP 指标怎么定义的",
        "查看 p95 延迟趋势",
        "业务转化率是多少",
        "Web Vitals 指标解读",
        "what is Apdex and how to collect it",
        "自定义指标怎么采集",
        "Dashboard 里 FID 数据异常",
        "按服务维度查看错误率趋势",
    ],
}

# ── Types ──────────────────────────────────────────────────────────────


@dataclass
class IntentCandidate:
    """A semantic match result for an intent category."""
    name: str
    score: float
    description: str = ""


@dataclass
class IntentSlots:
    """Structured intent classification result (replaces flat dict in intent_slots)."""
    domain: str = "general"
    primary_intent: str = "general"
    secondary_intents: list[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = "regex"  # "regex" | "semantic" | "llm"
    reason: str = ""
    metrics: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    requires_user_vector_context: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "intent": self.primary_intent,
            "secondary_intents": self.secondary_intents,
            "confidence": self.confidence,
            "source": self.source,
            "reason": self.reason,
            "metrics": self.metrics,
            "entities": self.entities,
            "requires_user_vector_context": self.requires_user_vector_context,
        }


@dataclass
class IntentRoutingResult:
    intent_slots: IntentSlots
    trace: list[dict[str, Any]]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && python -m pytest tests/test_intent_router.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/agent/intent_router.py backend/tests/test_intent_router.py
git commit -m "feat(intent-router): add IntentSlots schema, IntentCandidate, and INTENT_UTTERANCES"
```

---

### Task 2: 实现 Tier 0 — 正则意图匹配 + 置信度启发式

**Files:**
- Modify: `backend/src/personal_assistant/agent/intent_router.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intent_router.py (追加)
from personal_assistant.agent.intent_router import _regex_intent_with_confidence


def test_regex_intent_troubleshoot_high_confidence():
    """多关键词命中 → 高置信度"""
    intent, conf = _regex_intent_with_confidence("排查 payment-service 超时 根因分析")
    assert intent == "troubleshoot"
    assert conf >= 0.80


def test_regex_intent_patrol_single_keyword_low_confidence():
    """单关键词 → 低置信度"""
    intent, conf = _regex_intent_with_confidence("巡检")
    assert intent == "patrol"
    assert 0.60 <= conf < 0.80


def test_regex_intent_metrics_with_metric_names():
    """包含指标名 → 命中 metrics"""
    intent, conf = _regex_intent_with_confidence("帮我看下 p99 和 LCP")
    assert intent == "metrics"
    assert conf >= 0.80


def test_regex_intent_general_fallback():
    """不匹配任何意图 → general 低置信"""
    intent, conf = _regex_intent_with_confidence("今天天气怎么样")
    assert intent == "general"
    assert conf < 0.60


def test_regex_intent_audit():
    intent, conf = _regex_intent_with_confidence("审计一下合规情况")
    assert intent == "audit"
    assert conf >= 0.70
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_intent_router.py::test_regex_intent_troubleshoot_high_confidence -v
```
Expected: FAIL — function not defined

- [ ] **Step 3: Write minimal implementation**

```python
# 追加到 intent_router.py
import re

def _regex_intent_with_confidence(normalized: str) -> tuple[str, float]:
    """Tier 0: regex intent classification with confidence heuristics.

    Returns (intent, confidence). When confidence < 0.80, the caller
    should proceed to Tier 1 (semantic) instead of short-circuiting.
    """
    lowered = normalized.lower()

    # Count keyword signals per intent
    troubleshoot_signals = len(re.findall(
        r"\b(?:rca|root\s*cause|troubleshoot|timeout|slow|error)\b|排查|根因|超时|异常|故障|挂了",
        lowered,
    ))
    patrol_signals = len(re.findall(
        r"\b(?:patrol|health\s*check|alert)\b|巡检|告警|健康检查|监控",
        lowered,
    ))
    audit_signals = len(re.findall(
        r"\b(?:audit|approval|compliance|log)\b|审计|合规|审批|日志|治理",
        lowered,
    ))
    # Metrics: keyword matches OR metric name patterns
    metric_keywords = len(re.findall(
        r"\b(?:metric|web\s*vitals|conversion)\b|指标|转化率|趋势|解读|定义|怎么",
        lowered,
    ))
    metric_names = len(re.findall(
        r"\b(?:p50|p75|p90|p95|p99|lcp|cls|inp|ttfb|apdex|slo|fid)\b", lowered,
    ))
    metrics_signals = metric_keywords + metric_names

    # High confidence: 2+ signals
    if troubleshoot_signals >= 2:
        return ("troubleshoot", 0.90)
    if patrol_signals >= 2:
        return ("patrol", 0.90)
    if audit_signals >= 2:
        return ("audit", 0.90)
    if metrics_signals >= 2:
        return ("metrics", 0.90)

    # Medium confidence: exactly 1 signal
    if troubleshoot_signals == 1:
        return ("troubleshoot", 0.70)
    if patrol_signals == 1:
        return ("patrol", 0.70)
    if audit_signals == 1:
        return ("audit", 0.70)
    if metrics_signals == 1:
        return ("metrics", 0.70)

    # Fallback
    return ("general", 0.40)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && python -m pytest tests/test_intent_router.py -v -k "regex_intent"
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/agent/intent_router.py backend/tests/test_intent_router.py
git commit -m "feat(intent-router): add Tier 0 regex intent classification with confidence"
```

---

### Task 3: 实现 IntentEmbeddingIndex（内存版，复用 BGE-M3）

**Files:**
- Modify: `backend/src/personal_assistant/agent/intent_router.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intent_router.py (追加)
from personal_assistant.agent.intent_router import IntentEmbeddingIndex


class FakeEmbeddingProvider:
    """Fake embedding provider that returns deterministic vectors per text."""
    async def embed(self, text: str) -> list[float]:
        # Simple hash-based vector: different texts → different vectors
        h = hash(text) % 1000
        return [float((h + i) % 100) / 100.0 for i in range(8)]


def async_test(coro):
    """Helper to run async tests."""
    import asyncio
    return asyncio.run(coro)


def test_intent_index_warmup_and_search():
    """预热后可以搜索到匹配的意图"""
    provider = FakeEmbeddingProvider()
    index = IntentEmbeddingIndex(provider)

    async_test(index.warmup())

    candidates = async_test(index.search("排查服务超时问题", top_k=3))
    assert len(candidates) >= 1
    assert candidates[0].name in ("troubleshoot", "patrol", "audit", "metrics")
    assert 0.0 <= candidates[0].score <= 1.0


def test_intent_index_only_searches_defined_intents():
    """不会返回未定义的意图"""
    provider = FakeEmbeddingProvider()
    index = IntentEmbeddingIndex(provider)
    async_test(index.warmup())

    candidates = async_test(index.search("random query", top_k=10))
    for c in candidates:
        assert c.name in ("troubleshoot", "patrol", "audit", "metrics")


def test_intent_index_top_k_respected():
    provider = FakeEmbeddingProvider()
    index = IntentEmbeddingIndex(provider)
    async_test(index.warmup())

    candidates = async_test(index.search("anything", top_k=2))
    assert len(candidates) <= 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_intent_router.py::test_intent_index_warmup_and_search -v
```
Expected: FAIL — IntentEmbeddingIndex not defined

- [ ] **Step 3: Write minimal implementation**

```python
# 追加到 intent_router.py
import asyncio
import math

from personal_assistant.agent.router import SkillEmbeddingProvider


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class IntentEmbeddingIndex:
    """In-memory vector index for intent utterances.

    Embeds each utterance, computes the mean vector per intent (mean pooling),
    and compares query embeddings against intent centroids via cosine similarity.
    """

    def __init__(self, embedding_provider: SkillEmbeddingProvider) -> None:
        self.embedding_provider = embedding_provider
        self._intent_vectors: dict[str, list[float]] = {}

    async def warmup(self) -> None:
        """Pre-compute intent centroid vectors from utterance embeddings."""
        for intent, utterances in INTENT_UTTERANCES.items():
            vectors: list[list[float]] = []
            for utterance in utterances:
                vectors.append(await self.embedding_provider.embed(utterance))
            if not vectors:
                continue
            dim = len(vectors[0])
            avg = [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]
            self._intent_vectors[intent] = avg

    async def search(self, query: str, top_k: int = 5) -> list[IntentCandidate]:
        """Return intent candidates sorted by descending cosine similarity."""
        if not self._intent_vectors:
            return []
        query_vector = await self.embedding_provider.embed(query)
        candidates: list[IntentCandidate] = []
        for intent, vector in self._intent_vectors.items():
            score = _cosine_similarity(query_vector, vector)
            candidates.append(IntentCandidate(name=intent, score=score, description=""))
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:max(1, top_k)]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && python -m pytest tests/test_intent_router.py -v -k "intent_index"
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/agent/intent_router.py backend/tests/test_intent_router.py
git commit -m "feat(intent-router): add IntentEmbeddingIndex with mean-pooled intent centroids"
```

---

### Task 4: 实现 Tier 2 — LLM 意图分类器

**Files:**
- Modify: `backend/src/personal_assistant/agent/intent_router.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intent_router.py (追加)
from personal_assistant.agent.intent_router import (
    IntentDecision,
    _parse_intent_llm_decision,
    INTENT_CLASSIFIER_PROMPT,
)


class FakeLLM:
    """Fake LLM that returns structured JSON."""
    def __init__(self, response: dict):
        self.response = response

    async def ainvoke(self, messages, **kwargs):
        import json
        from langchain_core.messages import AIMessage
        return AIMessage(content=json.dumps(self.response, ensure_ascii=False))


def test_parse_intent_llm_decision_dict():
    """解析字典格式的 LLM 输出"""
    decision = _parse_intent_llm_decision({"primary_intent": "troubleshoot", "confidence": 0.9, "reason": "测试"})
    assert decision.primary_intent == "troubleshoot"
    assert decision.confidence == 0.9
    assert decision.reason == "测试"
    assert "metrics" in decision.secondary_intents  # troubleshoot → metrics 作为次要意图


def test_parse_intent_llm_decision_json_string():
    decision = _parse_intent_llm_decision('{"primary_intent": "patrol", "confidence": 0.85}')
    assert decision.primary_intent == "patrol"
    assert decision.confidence == 0.85


def test_parse_intent_llm_decision_invalid_fallback():
    """非法输入 → general fallback"""
    decision = _parse_intent_llm_decision(None)
    assert decision.primary_intent == "general"
    assert decision.confidence < 0.3


def test_parse_intent_llm_decision_markdown_code_block():
    decision = _parse_intent_llm_decision('```json\n{"primary_intent": "metrics", "confidence": 0.7}\n```')
    assert decision.primary_intent == "metrics"


def test_classifier_prompt_contains_all_intents():
    """Prompt 中必须包含所有意图类别定义"""
    assert "troubleshoot" in INTENT_CLASSIFIER_PROMPT
    assert "patrol" in INTENT_CLASSIFIER_PROMPT
    assert "audit" in INTENT_CLASSIFIER_PROMPT
    assert "metrics" in INTENT_CLASSIFIER_PROMPT
    assert "general" in INTENT_CLASSIFIER_PROMPT
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_intent_router.py::test_parse_intent_llm_decision_dict -v
```
Expected: FAIL — IntentDecision not defined

- [ ] **Step 3: Write minimal implementation**

```python
# 追加到 intent_router.py
import json as json_module
import logging

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class IntentDecision(BaseModel):
    """LLM structured output for intent classification."""
    primary_intent: str = Field(
        default="general",
        description="主意图: troubleshoot | patrol | audit | metrics | general",
    )
    secondary_intents: list[str] = Field(
        default_factory=list,
        description="次要意图",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


# Secondary intents for each primary intent — maps to the existing
# _supervisor_plan() logic so the downstream sub-agent scheduling
# automatically benefits from richer intent signals.
_PRIMARY_TO_SECONDARY: dict[str, list[str]] = {
    "troubleshoot": ["metrics", "audit"],
    "patrol": ["metrics", "audit"],
    "audit": ["metrics"],
    "metrics": [],
    "general": [],
}

INTENT_CLASSIFIER_PROMPT = """你是 APM 意图分类器。分析用户查询，输出结构化分类结果。

## 意图定义

- **troubleshoot**: 排查故障、根因分析、性能异常诊断、RCA
- **patrol**: 巡检规则配置、告警阈值设置、定时健康检查
- **audit**: 执行日志审计、SLA 合规检查、审批记录查询、安全事件审查
- **metrics**: 指标定义/解读、性能数据查询、Web Vitals、业务指标
- **general**: 以上都不匹配的通用查询

## 规则

- 用户可能同时有多个意图，主意图放 primary_intent，次要意图放 secondary_intents
- 如果确实无法判断，primary_intent 设为 "general"，confidence 设为 0.3 以下
- 只输出 JSON，不要解释"""


def _parse_intent_llm_decision(raw: Any) -> IntentDecision:
    """Parse LLM response into an IntentDecision, with robust fallback."""
    if raw is None:
        return IntentDecision(primary_intent="general", confidence=0.1, reason="empty response")

    text = ""
    if isinstance(raw, dict):
        text = json_module.dumps(raw)
    elif hasattr(raw, "content"):
        text = str(getattr(raw, "content", ""))
    else:
        text = str(raw)

    # Strip Markdown code fences
    text = text.strip()
    if text.startswith("```"):
        # Remove ```json ... ``` wrapper
        end = text.rfind("```")
        if end > 3:
            text = text[text.find("\n") + 1 : end].strip()

    try:
        data = json_module.loads(text)
    except json_module.JSONDecodeError:
        logger.warning("Intent LLM decision JSON parse failed: %s", text[:200])
        return IntentDecision(primary_intent="general", confidence=0.1, reason="json parse failed")

    if not isinstance(data, dict):
        return IntentDecision(primary_intent="general", confidence=0.1, reason="not a dict")

    primary = str(data.get("primary_intent") or "general")
    confidence = float(data.get("confidence") or 0.0)
    reason = str(data.get("reason") or "")

    # Validate primary intent is a known category
    valid_intents = {"troubleshoot", "patrol", "audit", "metrics", "general"}
    if primary not in valid_intents:
        primary = "general"
        confidence = min(confidence, 0.3)

    # Auto-populate secondary intents based on primary
    secondary = _PRIMARY_TO_SECONDARY.get(primary, [])

    # Also accept explicit secondary from LLM
    explicit = data.get("secondary_intents")
    if isinstance(explicit, list):
        for s in explicit:
            if isinstance(s, str) and s in valid_intents and s != "general" and s not in secondary:
                secondary.append(s)

    return IntentDecision(
        primary_intent=primary,
        secondary_intents=secondary,
        confidence=min(max(confidence, 0.0), 1.0),
        reason=reason,
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && python -m pytest tests/test_intent_router.py -v -k "parse_intent or classifier"
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/agent/intent_router.py backend/tests/test_intent_router.py
git commit -m "feat(intent-router): add Tier 2 LLM intent classifier with IntentDecision"
```

---

### Task 5: 实现 route_intent_with_trace() 三层漏斗编排

**Files:**
- Modify: `backend/src/personal_assistant/agent/intent_router.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intent_router.py (追加)
from personal_assistant.agent.intent_router import route_intent_with_trace


def test_route_intent_three_tier_trace():
    """三层漏斗产生完整 trace"""
    provider = FakeEmbeddingProvider()
    index = IntentEmbeddingIndex(provider)
    async_test(index.warmup())

    result = async_test(route_intent_with_trace(
        user_text="排查 checkout 服务 p99 超时问题",
        intent_index=index,
        llm=None,  # 不传 LLM → 仅走 Tier 0 + Tier 1
        regex_threshold=0.80,
        semantic_threshold=0.75,
    ))

    slots = result.intent_slots
    trace = result.trace

    assert slots.primary_intent in ("troubleshoot", "patrol", "audit", "metrics", "general")
    assert slots.source in ("regex", "semantic", "llm")
    assert len(trace) >= 1
    # 每个 trace entry 都有 stage 字段
    stages = {t["stage"] for t in trace}
    assert "regex" in stages


def test_route_intent_regex_short_circuit():
    """高置信度正则命中 → 短路，不进入语义层"""
    provider = FakeEmbeddingProvider()
    index = IntentEmbeddingIndex(provider)
    async_test(index.warmup())

    result = async_test(route_intent_with_trace(
        user_text="排查服务超时 根因分析 故障定位",
        intent_index=index,
        llm=None,
        regex_threshold=0.80,
        semantic_threshold=0.75,
    ))

    assert result.intent_slots.primary_intent == "troubleshoot"
    assert result.intent_slots.source == "regex"
    assert result.intent_slots.confidence >= 0.80


def test_route_intent_falls_through_to_semantic():
    """单关键词低置信 → 进入语义层"""
    provider = FakeEmbeddingProvider()
    index = IntentEmbeddingIndex(provider)
    async_test(index.warmup())

    result = async_test(route_intent_with_trace(
        user_text="巡检",  # just one keyword
        intent_index=index,
        llm=None,
        regex_threshold=0.80,
        semantic_threshold=0.75,
    ))

    assert result.intent_slots.primary_intent != "general"  # 应该命中某意图
    # trace 应包含 regex (below_threshold) + semantic
    stages = {t["stage"] for t in result.trace}
    assert "regex" in stages


def test_route_intent_without_index_uses_regex_only():
    """没有 semantic index 时仅走 Tier 0"""
    result = async_test(route_intent_with_trace(
        user_text="查询天气",
        intent_index=None,
        llm=None,
    ))

    assert result.intent_slots.primary_intent == "general"
    assert result.intent_slots.source == "regex"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_intent_router.py::test_route_intent_three_tier_trace -v
```
Expected: FAIL — route_intent_with_trace not defined

- [ ] **Step 3: Write minimal implementation**

```python
# 追加到 intent_router.py

async def route_intent_with_trace(
    user_text: str,
    *,
    intent_index: "IntentEmbeddingIndex | None" = None,
    llm=None,
    regex_threshold: float = 0.80,
    semantic_threshold: float = 0.75,
    semantic_top_k: int = 3,
    llm_threshold: float = 0.60,
    existing_slots: dict[str, Any] | None = None,
) -> IntentRoutingResult:
    """3-tier intent routing funnel: regex → semantic → LLM.

    Args:
        user_text: The user's query text.
        intent_index: Optional intent embedding index for Tier 1.
        llm: Optional LLM for Tier 2 classification.
        regex_threshold: Confidence >= this short-circuits after Tier 0.
        semantic_threshold: Cosine similarity >= this selects after Tier 1.
        semantic_top_k: Max candidates from semantic search.
        llm_threshold: Confidence >= this selects after Tier 2.
        existing_slots: Pre-extracted metrics/entities from legacy regex.

    Returns:
        IntentRoutingResult with classified intent and trace log.
    """
    normalized = " ".join(user_text.split())
    trace: list[dict[str, Any]] = []

    # ── Tier 0: Regex with confidence ─────────────────────────────────
    regex_intent, regex_conf = _regex_intent_with_confidence(normalized)

    # Merge any pre-extracted metrics/entities from the legacy regex
    metrics: list[str] = []
    entities: list[str] = []
    if isinstance(existing_slots, dict):
        metrics = list(existing_slots.get("metrics") or [])
        entities = list(existing_slots.get("entities") or [])

    if regex_conf >= regex_threshold and regex_intent != "general":
        trace.append({
            "stage": "regex",
            "status": "selected",
            "intent": regex_intent,
            "confidence": regex_conf,
            "threshold": regex_threshold,
        })
        return IntentRoutingResult(
            intent_slots=IntentSlots(
                domain=_looks_like_apm_domain(normalized),
                primary_intent=regex_intent,
                confidence=regex_conf,
                source="regex",
                reason=f"regex matched with {regex_conf:.0%} confidence",
                metrics=metrics,
                entities=entities,
            ),
            trace=trace,
        )

    trace.append({
        "stage": "regex",
        "status": "below_threshold" if regex_intent != "general" else "missed",
        "intent": regex_intent,
        "confidence": regex_conf,
        "threshold": regex_threshold,
    })

    # ── Tier 1: Semantic Router ──────────────────────────────────────
    if intent_index is not None and normalized.strip():
        try:
            candidates = await intent_index.search(normalized, top_k=semantic_top_k)
        except Exception:
            logger.exception("Intent semantic search failed")
            candidates = []
            trace.append({"stage": "semantic", "status": "failed", "reason": "search error"})

        if candidates:
            top = candidates[0]
            trace.append({
                "stage": "semantic",
                "status": "completed",
                "candidates": [{"name": c.name, "score": round(c.score, 4)} for c in candidates],
                "threshold": semantic_threshold,
            })
            if top.score >= semantic_threshold:
                trace[-1]["status"] = "selected"
                trace[-1]["selected_intent"] = top.name
                return IntentRoutingResult(
                    intent_slots=IntentSlots(
                        domain=_looks_like_apm_domain(normalized),
                        primary_intent=top.name,
                        confidence=top.score,
                        source="semantic",
                        reason=f"semantic match score={top.score:.3f}",
                        metrics=metrics,
                        entities=entities,
                    ),
                    trace=trace,
                )
            if top.score < semantic_threshold:
                trace[-1]["status"] = "below_threshold"
        else:
            trace.append({"stage": "semantic", "status": "no_candidates"})
    elif intent_index is None:
        trace.append({"stage": "semantic", "status": "skipped", "reason": "no intent index"})

    # ── Tier 2: LLM Classifier ───────────────────────────────────────
    if llm is not None:
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            response = await llm.ainvoke([
                SystemMessage(content=INTENT_CLASSIFIER_PROMPT),
                HumanMessage(content=(
                    f"用户查询: {normalized}\n"
                    f"已提取的指标: {metrics}\n"
                    f"已提取的实体: {entities}"
                )),
            ])
            decision = _parse_intent_llm_decision(response)
        except Exception:
            logger.exception("Intent LLM classification failed")
            decision = IntentDecision(primary_intent="general", confidence=0.1, reason="llm invocation failed")

        trace.append({
            "stage": "llm_judge",
            "status": "selected" if decision.confidence >= llm_threshold else "below_threshold",
            "primary_intent": decision.primary_intent,
            "secondary_intents": decision.secondary_intents,
            "confidence": decision.confidence,
            "reason": decision.reason,
            "threshold": llm_threshold,
        })

        return IntentRoutingResult(
            intent_slots=IntentSlots(
                domain=_looks_like_apm_domain(normalized),
                primary_intent=decision.primary_intent,
                secondary_intents=decision.secondary_intents,
                confidence=decision.confidence,
                source="llm",
                reason=decision.reason,
                metrics=metrics,
                entities=entities,
            ),
            trace=trace,
        )

    trace.append({"stage": "llm_judge", "status": "skipped", "reason": "no llm available"})

    # ── Fallback ──────────────────────────────────────────────────────
    return IntentRoutingResult(
        intent_slots=IntentSlots(
            domain=_looks_like_apm_domain(normalized),
            primary_intent=regex_intent,
            confidence=regex_conf,
            source="regex",
            reason="fallback: regex result used",
            metrics=metrics,
            entities=entities,
        ),
        trace=trace,
    )


def _looks_like_apm_domain(text: str) -> str:
    """Heuristic domain detection for APM vs general."""
    if re.search(
        r"\b(?:apm|api|p95|p99|lcp|cls|inp|apdex|slo|rca|patrol|troubleshoot)\b|"
        r"排查|根因|巡检|告警|指标|审计|合规",
        text, re.I,
    ):
        return "apm"
    return "general"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && python -m pytest tests/test_intent_router.py -v -k "route_intent"
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/agent/intent_router.py backend/tests/test_intent_router.py
git commit -m "feat(intent-router): add route_intent_with_trace 3-tier funnel orchestrator"
```

---

### Task 6: 添加 MULTI_AGENT_INTENT_* 配置项

**Files:**
- Modify: `backend/src/personal_assistant/config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py (追加)
def test_multi_agent_intent_settings_defaults(monkeypatch):
    """默认值检查"""
    from personal_assistant.config import Settings
    
    # 清除数据库 URL 要求
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    
    settings = Settings()
    assert settings.multi_agent_intent_regex_threshold == 0.80
    assert settings.multi_agent_intent_semantic_enabled is True
    assert settings.multi_agent_intent_semantic_threshold == 0.75
    assert settings.multi_agent_intent_llm_enabled is True
    assert settings.multi_agent_intent_llm_threshold == 0.60
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_config.py::test_multi_agent_intent_settings_defaults -v
```
Expected: FAIL — AttributeError

- [ ] **Step 3: Write minimal implementation**

```python
# 追加到 config.py Settings 类中
multi_agent_intent_regex_threshold: float = Field(
    default=0.80,
    alias="MULTI_AGENT_INTENT_REGEX_THRESHOLD",
)
multi_agent_intent_semantic_enabled: bool = Field(
    default=True,
    alias="MULTI_AGENT_INTENT_SEMANTIC_ENABLED",
)
multi_agent_intent_semantic_threshold: float = Field(
    default=0.75,
    alias="MULTI_AGENT_INTENT_SEMANTIC_THRESHOLD",
)
multi_agent_intent_llm_enabled: bool = Field(
    default=True,
    alias="MULTI_AGENT_INTENT_LLM_ENABLED",
)
multi_agent_intent_llm_threshold: float = Field(
    default=0.60,
    alias="MULTI_AGENT_INTENT_LLM_THRESHOLD",
)
multi_agent_intent_llm_model: str | None = Field(
    default=None,
    alias="MULTI_AGENT_INTENT_LLM_MODEL",
)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && python -m pytest tests/test_config.py::test_multi_agent_intent_settings_defaults -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/config.py backend/tests/test_config.py
git commit -m "feat(config): add MULTI_AGENT_INTENT_* settings for hybrid intent routing"
```

---

### Task 7: 改造 compile_multi_agent — 接入三层漏斗

**Files:**
- Modify: `backend/src/personal_assistant/agent/multi_agent.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intent_router.py (追加)
from personal_assistant.agent.multi_agent import _build_intent_slots_from_result


def test_build_intent_slots_from_result_backward_compatible():
    """IntentSlots.to_dict() 产生的 slots 与旧格式兼容"""
    from personal_assistant.agent.intent_router import IntentSlots

    slots = IntentSlots(
        domain="apm",
        primary_intent="troubleshoot",
        secondary_intents=["metrics", "audit"],
        confidence=0.90,
        source="regex",
        metrics=["p99"],
        entities=["checkout"],
    )
    result = slots.to_dict()

    # 旧代码依赖的字段：
    assert result["intent"] == "troubleshoot"  # _supervisor_plan 用这个
    assert result["domain"] == "apm"
    assert result["metrics"] == ["p99"]
    assert result["entities"] == ["checkout"]
    assert result["requires_user_vector_context"] is True
    # 新字段也在
    assert result["confidence"] == 0.90
    assert result["source"] == "regex"
```

- [ ] **Step 2: Run test to verify it fails**

(这个测试应该直接 pass，因为 `to_dict()` 已经在 Task 1 中定义。但这作为合同测试确保不改坏。)

Expected: PASS (contract test)

- [ ] **Step 3: Modify compile_multi_agent**

Now modify `multi_agent.py:compile_multi_agent` to accept intent router params and use them in `rewrite_intent`:

```python
# multi_agent.py — 修改函数签名和 rewrite_intent 节点

def compile_multi_agent(
    settings: Settings,
    registry: SkillRegistry,
    memory,
    llm_config: LLMConfig | None = None,
    hook_manager=None,
    cache=None,
    # 新增：intent routing params
    intent_index=None,  # IntentEmbeddingIndex | None
    intent_llm=None,    # LLM for Tier 2 intent classification
):
    llm = build_llm(settings, llm_config)

    # 读取配置
    regex_threshold = getattr(settings, "multi_agent_intent_regex_threshold", 0.80)
    semantic_enabled = getattr(settings, "multi_agent_intent_semantic_enabled", True)
    semantic_threshold = getattr(settings, "multi_agent_intent_semantic_threshold", 0.75)
    llm_enabled = getattr(settings, "multi_agent_intent_llm_enabled", True)
    llm_threshold = getattr(settings, "multi_agent_intent_llm_threshold", 0.60)

    from personal_assistant.agent.intent_router import (
        route_intent_with_trace,
    )

    async def rewrite_intent(state: AgentState, config: RunnableConfig | None = None) -> AgentState:
        query = _last_human_text(state)

        # 保留旧的纯正则逻辑用于提取 metrics/entities
        legacy = rewrite_query_and_slots(query)

        # 运行三层漏斗
        routing = await route_intent_with_trace(
            query,
            intent_index=intent_index if semantic_enabled else None,
            llm=intent_llm if llm_enabled else None,
            regex_threshold=regex_threshold,
            semantic_threshold=semantic_threshold,
            llm_threshold=llm_threshold,
            existing_slots=legacy.get("slots"),
        )

        slots_dict = routing.intent_slots.to_dict()
        await _record_multiagent_log(memory, config, "rewrite_intent", output={
            "slots": slots_dict,
            "trace": routing.trace,
        })
        return {
            "rewritten_query": legacy["rewritten_query"],
            "intent_slots": slots_dict,
        }
```

**Key:** `rewrite_query_and_slots()` is still called (for metrics/entities extraction), but the intent classification now goes through the 3-tier funnel. The `to_dict()` output maintains backward compatibility with `_supervisor_plan()` and all downstream consumers.

- [ ] **Step 4: Run ALL existing multi-agent tests to verify no regression**

```bash
cd backend && python -m pytest tests/test_multi_agent_graph.py tests/test_multi_agent_contract.py tests/test_multi_agent_evaluation.py -v
```
Expected: ALL PASS (no regression)

- [ ] **Step 5: Run new intent router tests**

```bash
cd backend && python -m pytest tests/test_intent_router.py -v
```
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/personal_assistant/agent/multi_agent.py backend/tests/test_intent_router.py
git commit -m "feat(multi-agent): wire 3-tier intent routing into rewrite_intent node"
```

---

### Task 8: 修改 harness.py — 传递 intent router 依赖到 compile_multi_agent

**Files:**
- Modify: `backend/src/personal_assistant/agent/harness.py`

- [ ] **Step 1: Modify _compile_multi_agent**

```python
# harness.py:745 — _compile_multi_agent() 中添加 intent routing 依赖

def _compile_multi_agent(self, llm_config: LLMConfig | None):
    from personal_assistant.agent import multi_agent as multi_agent_module
    from personal_assistant.agent.intent_router import IntentEmbeddingIndex
    from personal_assistant.agent.router import OllamaBgeM3EmbeddingProvider

    kwargs = {}
    if self.cache is not None:
        kwargs["cache"] = self.cache

    # Build intent index if semantic routing is enabled
    if getattr(self.settings, "multi_agent_intent_semantic_enabled", True):
        embedding_provider = OllamaBgeM3EmbeddingProvider(
            base_url=self.settings.skill_routing_ollama_base_url,
            model=self.settings.skill_routing_embedding_model,
        )
        intent_index = IntentEmbeddingIndex(embedding_provider)
        kwargs["intent_index"] = intent_index
    else:
        kwargs["intent_index"] = None

    # Build intent LLM if Tier 2 is enabled
    if getattr(self.settings, "multi_agent_intent_llm_enabled", True):
        intent_llm_model = getattr(self.settings, "multi_agent_intent_llm_model", None)
        kwargs["intent_llm"] = build_llm(
            self.settings,
            LLMConfig(model=intent_llm_model) if intent_llm_model else llm_config,
        )
    else:
        kwargs["intent_llm"] = None

    return multi_agent_module.compile_multi_agent(
        self.settings,
        self.registry,
        self.memory,
        llm_config,
        hook_manager=self.hook_manager,
        **kwargs,
    )
```

- [ ] **Step 2: Run full test suite**

```bash
cd backend && python -m pytest tests/ -v --tb=short 2>&1 | tail -60
```
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add backend/src/personal_assistant/agent/harness.py
git commit -m "feat(harness): wire intent index and LLM into compile_multi_agent"
```

---

## 自检清单

1. **Spec coverage:** 路由.md 5.4 节的所有设计要点均有对应任务 — ✅
2. **Placeholder scan:** 无 TBD/TODO/implement later — ✅
3. **Type consistency:** `IntentSlots.to_dict()` 输出 key `"intent"` 兼容下游 `_supervisor_plan()` — ✅
4. **不影响 single-agent:** single-agent 的 `router.py`、`agent.py`、`build_skill_router()` 完全不修改 — ✅
