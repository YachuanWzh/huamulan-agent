"""Multi-agent intent routing with a 3-tier funnel (regex → semantic → LLM).

Reuses the single-agent embedding infrastructure (OllamaBgeM3EmbeddingProvider,
OllamaBgeM3Reranker) from router.py.

The key difference from single-agent routing:
  - Single-agent: embeds *skill metadata* (name + description + triggers)
    to match user query → specific Skill
  - Multi-agent: embeds *intent utterances* (example queries per category)
    to classify user query → intent category (troubleshoot/patrol/audit/metrics)
"""
from dataclasses import dataclass, field
from typing import Any


# ── Intent utterances (Tier 1 semantic matching) ──────────────────────────
# Each intent category has example user queries in Chinese and English.
# These are embedded via BGE-M3 and mean-pooled to form an intent centroid
# vector. At query time the user's query embedding is compared against
# each centroid via cosine similarity.
#
# Reuses the same OllamaBgeM3EmbeddingProvider from router.py — no new
# embedding infrastructure needed.

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


# ── Types ──────────────────────────────────────────────────────────────────


@dataclass
class IntentCandidate:
    """A semantic match result for an intent category (Tier 1 output)."""
    name: str
    score: float
    description: str = ""


@dataclass
class IntentSlots:
    """Structured intent classification result.

    Replaces the flat dict currently stored in AgentState.intent_slots.
    The to_dict() method maintains backward compatibility with the
    existing 'intent' key that _supervisor_plan() consumes.
    """
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
        """Convert to dict compatible with legacy intent_slots consumers.

        Key compatibility: 'intent' maps from primary_intent so
        _supervisor_plan() works without modification.
        """
        return {
            "domain": self.domain,
            "intent": self.primary_intent,  # ← legacy key
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
    """Result of the 3-tier intent routing funnel with diagnostic trace."""
    intent_slots: IntentSlots
    trace: list[dict[str, Any]]
