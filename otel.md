# OTEL 遥测数据推送方案

> **状态**：规划阶段  
> **日期**：2026-07-05  
> **关联文档**：[技术方案报告.md §OTEL 远程遥测数据集成](./技术方案报告.md#otel-远程遥测数据集成)

## 现状

当前 langgraph-claw 通过 `otel-query` Skill 主动拉取（pull）远端 OpenTelemetry Demo 的 Jaeger traces 和 Prometheus metrics 进行分析。用户发起对话 → Agent 调用 `query_traces` / `query_metrics` → `apm.py` 分析。这套模式适合按需排障，但有两个局限：

1. **被动响应**：只有用户问了才查，无法在问题发生时主动发现
2. **全量拉取成本高**：每次分析都重新拉数据，无法增量消费

## 目标架构：分级推送

核心思路：**告警走快车道，数据走慢车道。**

```
                        ┌─────────────────────────────────┐
                        │     OpenTelemetry Demo           │
                        │     (远端服务器)                   │
                        └────────┬───────────────┬────────┘
                                 │               │
                                 ▼               ▼
                     ┌──────────────────┐  ┌──────────────────┐
                     │  OTel Collector  │  │  OTel Collector  │
                     │  (kafka exp.)    │  │  (现有 pipeline) │
                     └────────┬─────────┘  └────────┬─────────┘
                              │                     │
                              ▼                     ▼
                         ┌─────────┐        ┌──────────────┐
                         │  Kafka  │        │  Prometheus   │
                         │ (全量)   │        │  AlertManager │
                         └────┬────┘        └──────┬───────┘
                              │                    │
                              │            ┌───────┴───────┐
                              │            ▼               ▼
                              │      ┌──────────┐   ┌──────────┐
                              │      │ P0 / P1  │   │ P2 / P3  │
                              │      │ Webhook  │   │  数据    │
                              │      │ 即时推送  │   │          │
                              │      └────┬─────┘   └────┬─────┘
                              │           │              │
                              ▼           ▼              ▼
                    ┌──────────────────────────────────────────┐
                    │           langgraph-claw                 │
                    │                                          │
                    │  POST /api/otel/alerts  ← AlertManager   │
                    │  Kafka consumer (cron)  ← Kafka          │
                    │                                          │
                    │  ┌─────────────────────────────────┐     │
                    │  │ 分级路由                          │     │
                    │  │                                  │     │
                    │  │ P0/P1 → troubleshoot_agent (即时) │     │
                    │  │ P2    → patrol_agent (每 5min)    │     │
                    │  │ P3    → audit_agent (每 30min)    │     │
                    │  └─────────────────────────────────┘     │
                    └──────────────────────────────────────────┘
```

## 分级定级方法论

### 核心原则：三维决策模型

每一类异常用三个维度打分后落入对应级别——**不是拍脑袋定阈值，而是结构化的判定流程**。

```
                  紧急度 (Time-to-Action)
                  ↑
                  │  P0: 立刻 (秒级)      ← 用户正在受损
                  │  P1: 尽快 (分钟级)    ← 用户即将受损或已可感知
                  │  P2: 按周期 (5-15min) ← 趋势劣化，尚未影响用户
                  │  P3: 不急 (30min+)    ← 治理/合规/容量规划
                  │
                  └─────────────────────────→ 确定性 (Signal Confidence)

                  影响面 (Blast Radius) 决定级别上下修正：
                  P0 = 用户直接受损 + 大面积 + 确定故障
                  P1 = 用户可感知 + 局部 + 确定异常
                  P2 = 潜在劣化 + 统计推断
                  P3 = 治理/合规 + 无即时影响
```

### 按信号类型的判定矩阵

#### 可用性信号

| 信号 | P0 | P1 | P2 | P3 |
|------|:--:|:--:|:--:|:--:|
| `up == 0` 持续 1min+ | ✅ | — | — | — |
| `up == 0` 间歇性 flapping（10min 内 >3 次） | — | ✅ | — | — |
| 健康检查成功率 < 95% | — | ✅ | — | — |
| 健康检查成功率 < 99% 持续 1h | — | — | — | ✅ |

**逻辑**：可用性是最硬的信号——没有模糊带，`up == 0` 永远是 P0。

#### 延迟信号（以各服务 SLO 为锚点）

| 信号 | P0 | P1 | P2 | P3 |
|------|:--:|:--:|:--:|:--:|
| P95 > SLO × **5x** | ✅ | — | — | — |
| P95 > SLO × **2x** | — | ✅ | — | — |
| P95 > SLO × **1.2x** | — | — | — | ✅ |
| P95 导数 > 0.5ms/min 连续 3 个窗口 | — | — | ✅ | — |

**逻辑**：延迟有"量变到质变"的连续谱。阈值用 SLO 的**倍数**而非绝对值——不同服务的 SLO 不同（frontend 200ms vs accounting 2000ms），但"超过 SLO 5 倍"对任何服务都是 P0。

#### 错误率信号

| 信号 | P0 | P1 | P2 | P3 |
|------|:--:|:--:|:--:|:--:|
| 5xx > **50%** | ✅ | — | — | — |
| 5xx > **10%** | — | ✅ | — | — |
| 4xx + 5xx > **5%** | — | ✅ | — | — |
| 错误率 7 日趋势上升（导数法） | — | — | ✅ | — |
| 错误预算剩余 < 50% | — | — | — | ✅ |

**逻辑**：50% 5xx → 服务基本不可用，P0。5-10% → 严重劣化但服务还在跑，P1。

#### 资源信号

| 信号 | P0 | P1 | P2 | P3 |
|------|:--:|:--:|:--:|:--:|
| DB 连接池 > 95% | ✅ | — | — | — |
| 内存 > 95% | — | ✅ | — | — |
| CPU > 90% 持续 10min | — | ✅ | — | — |
| 内存/CPU 趋势上升（导数 > 阈值） | — | — | ✅ | — |
| 磁盘 > 80% | — | — | — | ✅ |

### 交叉验证规则（防止误报）

单一信号触发告警后，**必须交叉验证**才能确认级别——这是防止"孤立 P95 抖动就触发 P0 RCA"浪费 Token 的关键：

| 主告警 | 交叉验证信号 | 修正 |
|--------|-------------|------|
| P95 延迟飙升 | 查同时间段的 error rate、DB 慢查询数、Redis 命中率 | 如果全部正常 → 降级到 P2（可能是流量尖刺） |
| 5xx 错误率飙升 | 上游服务的 trace 是否有级联失败、DB 连接池状态 | 如果有级联失败 → 根因可能是下游，升级为交叉告警 |
| ServiceDown | 下游依赖是否也 Down | 区分"根因"还是"受害者" |
| 内存/CPU 飙升 | 同时间流量是否尖刺、GC 频率、OOM kill 事件 | 如果伴随流量尖刺 → P1；孤立 → P2 |

**交叉验证的数据源**：langgraph-claw 收到告警后自动从 Jaeger 拉 trace、从 Prometheus 拉关联指标，进行多信号交叉确认。确认后执行 RCA；确认不通过则标记为 `false_alarm` 记入审计日志。

### 服务 SLO 锚定

定级的数值阈值不是凭空拍的，而是以每个服务的 SLO 为锚点。SLO 因服务在链路中的位置而异：

| 层级 | 服务 | P95 SLO | 可用性 SLO | 说明 |
|------|------|---------|-----------|------|
| **Tier 0 — 用户面** | frontend, frontend-proxy | 200ms / 100ms | 99.9% / 99.95% | 延迟最敏感，用户直接感知 |
| **Tier 1 — 核心链路** | checkout, cart, payment, product-catalog | 300-500ms | 99.9-99.95% | 交易一致性最关键 |
| **Tier 2 — 支撑服务** | recommendation, ad, shipping, currency, email | 300-1000ms | 99.5-99.9% | 允许稍高延迟 |
| **Tier 3 — 后台服务** | accounting, fraud-detection, quote | 1000-2000ms | 99.0-99.5% | 延迟容忍度最高 |

> SLO 值的来源：① Grafana 历史基线（过去 7 天 P99.9）、② 用户影响映射（延迟 > X ms 时投诉开始出现）、③ 故障演练校准。详见 opentelemetry-demo 侧的 `src/prometheus/slo-targets.yml`。

### 定级决策流程图

```
收到告警信号
    │
    ├─ 用户是否直接受损？
    │   ├─ 是：服务不可用、页面白屏、下单失败、5xx > 50%
    │   │   └─ → P0 (立即唤醒 troubleshoot_agent + 通知)
    │   │
    │   └─ 否：用户可能感知到变慢/部分失败
    │       │
    │       ├─ 是否有级联风险？该服务是核心链路节点
    │       │   │   (checkout/cart/payment/frontend)
    │       │   ├─ 是 + 异常确定（error_rate > SLO, 5xx > 10%）
    │       │   │   └─ → P1 (尽快唤醒 troubleshoot_agent)
    │       │   └─ 否
    │       │       │
    │       │       ├─ 是确定异常还是统计趋势？
    │       │       │   ├─ 确定异常（指标突破 SLO×2）→ P1
    │       │       │   └─ 统计趋势（导数上升、早期信号）→ P2
    │       │       │
    │       │       └─ 是治理/合规问题？
    │       │           ├─ 错误预算、SLO 合规漂移 → P3
    │       │           └─ 资源水位、span 完整性 → P3
```

### 阈值校准流程

阈值不是一次写死的——需要持续校准：

```
Week 1: 用初版阈值上线，收集告警数据
    │
    ├─ P0 每天 > 5 次？ → 阈值太敏感，上调 for 等待时间或倍数
    ├─ P1 整周 0 次？   → 阈值太宽松，下调倍数 ×2 → ×1.5
    ├─ P2 告警数量是 P1 的 10x？ → P2 阈值太窄，上调导数门槛
    └─ P3 误报率 > 50%？ → 检查规则表达式是否有边界条件 bug
    │
Week 2: 调整后重新上线
    │
    └─ 每个 sprint 回顾一次告警质量：signal-to-noise ratio
```

## 分级定义（速查表）

| 级别 | 条件 | 通道 | 消费延迟 | 处理 Agent |
|------|------|------|----------|------------|
| **P0** | 服务不可用、DB 连接池耗尽、5xx > 50%、P95 > SLO×5 | AlertManager → Webhook → 即时 | < 30s | `troubleshoot_agent` 自动 RCA + 通知 |
| **P1** | P95 超 SLO 2x、错误率 > 5%、断路器打开 | AlertManager → Webhook → 即时 | < 1min | `troubleshoot_agent` 自动 RCA |
| **P2** | 延迟趋势上升、慢查询增多、内存泄漏早期信号 | Kafka → Cron batch | < 5min | `patrol_agent` 趋势分析 |
| **P3** | 日常巡检、资源水位、SLO 合规、span 属性完整性 | Kafka → Cron batch | < 30min | `audit_agent` 治理审计 |

## 各阶段实施计划

### Phase 1：OTel Collector → Kafka（全量遥测入队列）

**目标**：远端所有 trace + metric 写入 Kafka，不丢数据。

**远端改动**（OTel Collector 配置）：

```yaml
# 在现有 otelcol-config-observability.yml 的基础上叠加导出到 Kafka
exporters:
  kafka:
    brokers:
      - kafka:9092
    topic: otel-spans
    encoding: otlp_proto       # 保持 OTLP 二进制格式，不需要二次序列化
```

**基础设施**：部署 Kafka 集群（或单节点用于原型阶段）。

### Phase 2：langgraph-claw Kafka Consumer

**目标**：Cron 定时消费 Kafka 中的遥测数据，批量分析。

**langgraph-claw 改动**：

- 新增 `backend/src/personal_assistant/consumers/kafka_consumer.py`
  - 使用 `aiokafka` 消费 Kafka topic
  - 按 service + time window 聚合 trace
  - 调用 `apm.build_observability_snapshot()` 生成快照
  - 持久化到 PostgreSQL，供后续查询和趋势展示
- 新增 Cron 调度（APScheduler 或简单 asyncio loop）
  - P2 级别：每 5 分钟消费一次
  - P3 级别：每 30 分钟消费一次

```python
# 伪代码示意
async def consume_and_analyze(window: str = "5m"):
    """从 Kafka 拉取最近一个窗口的 trace/metric，批量分析。"""
    traces = await kafka_client.fetch(service="*", window=window)
    for service_name, service_traces in traces.group_by_service():
        events = []
        for trace in service_traces:
            events.extend(from_jaeger_trace(trace))
        snapshot = build_observability_snapshot(events, ...)
        await save_snapshot(snapshot)
        if snapshot.anomalies:
            await patrol_agent.analyze(snapshot)
```

### Phase 3：AlertManager Webhook 即时推送（P0/P1）

**目标**：P0/P1 告警不经过 Kafka，直推到 langgraph-claw 触发即时 RCA。

**远端改动**（Prometheus AlertManager）：

```yaml
# alertmanager.yml
route:
  receiver: langgraph-claw
  routes:
    - match:
        severity: critical      # P0
      receiver: langgraph-claw
      continue: true
    - match:
        severity: warning       # P1
      receiver: langgraph-claw

receivers:
  - name: langgraph-claw
    webhook_configs:
      - url: http://<claw-host>:8000/api/otel/alerts
        send_resolved: true
```

**Prometheus 告警规则示例**：

```yaml
# prometheus-rules.yml
groups:
  - name: sre-critical
    rules:
      - alert: ServiceDown
        expr: up{job!=""} == 0
        for: 1m
        labels:
          severity: critical    # → P0
        annotations:
          summary: "{{ $labels.service_name }} is down"
          runbook_url: "..."

      - alert: HighLatency
        expr: |
          histogram_quantile(0.95,
            rate(http_server_duration_milliseconds_bucket[5m])
          ) > 500
        labels:
          severity: warning     # → P1
        annotations:
          summary: "{{ $labels.service_name }} P95 > 500ms"
          description: "current value: {{ $value }}ms"
```

**langgraph-claw 新增端点**：

```python
# backend/src/personal_assistant/api/server.py

from pydantic import BaseModel

class AlertManagerWebhook(BaseModel):
    """AlertManager webhook payload (v4 format)."""
    receiver: str
    status: str             # "firing" | "resolved"
    alerts: list[Alert]
    groupLabels: dict
    commonLabels: dict
    commonAnnotations: dict

class Alert(BaseModel):
    status: str
    labels: dict            # service_name, severity, alertname, ...
    annotations: dict       # summary, description, runbook_url, ...
    startsAt: str
    endsAt: str

@app.post("/api/otel/alerts")
async def handle_otel_alert(payload: AlertManagerWebhook):
    """接收 AlertManager webhook，P0/P1 即时触发 RCA。"""
    for alert in payload.alerts:
        severity = alert.labels.get("severity", "P2")
        if severity not in ("critical", "warning"):
            continue  # P2/P3 走 Kafka 通道，这里只处理即时告警

        service = alert.labels.get("service_name", "unknown")
        metric = alert.labels.get("alertname", "unknown")

        # 自动拉取告警时间窗口内的相关遥测
        traces = query_jaeger_traces(service=service, lookback="15m")
        metrics = query_prometheus_metrics(promql=build_promql(alert))

        # 即时 RCA
        snapshot = build_observability_snapshot(
            rum_events=from_jaeger_trace(traces),
            execution_logs=from_prometheus_metric(metric, metrics),
        )

        # 异步执行排障 Agent
        background_tasks.add_task(
            run_auto_troubleshoot,
            alert=alert,
            snapshot=snapshot,
            notify=True,   # 推送诊断结论到通知通道
        )

    return {"status": "accepted", "alerts": len(payload.alerts)}
```

### Phase 4：分级路由与自动闭环

**目标**：根据严重级别自动路由到不同 Agent，实现端到端无人值守。

```
                    ┌──────────────────────────┐
                    │   langgraph-claw          │
                    │                           │
  AlertManager ──→  │ POST /api/otel/alerts    │
                    │   │                       │
                    │   ├─ P0 (critical)        │
                    │   │   → troubleshooot    │
                    │   │   → notify (即时)     │
                    │   │                       │
                    │   ├─ P1 (warning)         │
                    │   │   → troubleshooot    │
                    │   │   → notify (即时)     │
                    │   │                       │
  Kafka ──cron──→   │   ├─ P2                  │
                    │   │   → patrol_agent     │
                    │   │   → save_snapshot    │
                    │   │                       │
                    │   └─ P3                  │
                    │       → audit_agent      │
                    │       → save_snapshot    │
                    │                           │
                    └──────────────────────────┘
```

**自动通知通道**（按需扩展）：

| 通道 | P0 | P1 | P2 | P3 |
|------|----|----|----|-----|
| 即时消息（钉钉/飞书/Slack） | ✅ | ✅ | ❌ | ❌ |
| 工单系统 | ✅ | ❌ | ❌ | ❌ |
| 巡检报告 | ❌ | ❌ | ✅ | ✅ |
| 邮件汇总 | ❌ | ❌ | ❌ | ✅ |

## 依赖与风险

| 项目 | 说明 |
|------|------|
| **Kafka** | 原型阶段可单节点部署，生产需集群。OTel Collector 的 kafka exporter 原生支持 |
| **AlertManager** | Prometheus 生态标准组件，OTel Demo 已部署 Prometheus，只需加规则文件 |
| **aiokafka** | Python Kafka 异步客户端，与 FastAPI asyncio 兼容 |
| **通知通道** | 初期日志打印即可，后续接钉钉/飞书 webhook |

**主要风险**：

1. **告警风暴**：大量 P0/P1 同时涌入 → 令牌桶限流（每服务每分钟最多 1 次 RCA）
2. **Kafka 消费延迟**：Cron 窗口内数据量过大 → 按 service 分片，并行消费
3. **误报消耗 Token**：非真实故障的告警触发无效 RCA → 在 `troubleshoot_agent` prompt 中要求先判断是否为真实故障

## 与现有能力的衔接

| 现有能力 | 在推送方案中的角色 |
|----------|-------------------|
| `otel-query` Skill | 告警触发后自动调用，拉取告警时间窗口内的关联 trace/metric |
| `apm.py` 分析引擎 | 将 Kafka 中消费的 trace/metric 转换为 `ObservabilitySnapshot` |
| `troubleshoot_agent` | P0/P1 即时 RCA 的执行者 |
| `patrol_agent` | P2 Kafka cron 消费后的趋势分析执行者 |
| `audit_agent` | P3 Kafka cron 消费后的治理审计执行者 |
| `multi_agent.py` | 分级路由的编排层，根据 severity 决定调度策略 |
| ClawEval Golden Dataset | 新增 `otel_push_*.jsonl` 用例，验证推送→自动分析链路的正确性 |
