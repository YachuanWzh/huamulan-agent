# Plan: OTEL 数据查询工具和转换器

**目标:** 使 langgraph-claw 的 APM agent 能直接从 OpenTelemetry Demo 拉取真实遥测数据 (traces/metrics) 并进行分析。

**假设:**
- OTEL demo 服务地址: 通过 `OTEL_JAEGER_API_URL` 和 `OTEL_PROMETHEUS_PROXY_URL` 环境变量配置，详见 `.env.example`
- Jaeger API 路径前缀: `/jaeger/ui/api/`
- Prometheus 通过 Grafana 代理: `/api/datasources/proxy/uid/webstore-metrics/api/v1/`
- 新 Skill 名: `otel-query`

---

## Task 1: OTEL 数据转换器 (`apm.py`)

**文件:** `backend/src/personal_assistant/apm.py`
**测试:** `backend/tests/test_otel_query.py`

新增两个转换函数：

```python
def from_jaeger_trace(trace_data: dict) -> list[FrontendRumEvent]:
    """将 Jaeger trace 的 spans 转为 FrontendRumEvent 列表。
    
    映射规则：
    - HTTP span (tags 包含 http.method) → FrontendRumEvent(type="web_vital" 或判断)
    - Error span (tags 包含 error=true 或 http.status_code >= 400) → type="js_error"
    - span.duration 转为 value(ms)
    - span.operationName → name
    - tags["http.target"] → url
    """
    ...

def from_prometheus_metric(metric_name: str, prometheus_result: dict) -> list[ExecutionLog]:
    """将 Prometheus 查询结果转为 ExecutionLog 列表。
    
    映射规则：
    - http_server_duration_milliseconds_bucket → ExecutionLog(event_type="tool", name=endpoint, duration_ms=p95)
    - demo_*_total → ExecutionLog(event_type="turn", name=metric_name)
    - rpc_server_duration_* → ExecutionLog(event_type="tool", name=rpc_method)
    """
    ...

def from_jaeger_trace_to_logs(trace_data: dict) -> list[ExecutionLog]:
    """将 Jaeger trace 的 spans 转为 ExecutionLog 列表。"""
    ...
```

**TDD 步骤:**
1. RED: 写测试，用真实的 Jaeger trace JSON 验证转换结果
2. GREEN: 实现转换函数
3. REFACTOR: 提取公共逻辑

---

## Task 2: OTEL Query Skill (`otel-query`)

**文件:** 
- `backend/src/personal_assistant/skills/otel-query/SKILL.md`
- `backend/src/personal_assistant/skills/otel-query/scripts/query_traces.py`
- `backend/src/personal_assistant/skills/otel-query/scripts/query_metrics.py`

**SKILL.md:**
```markdown
---
name: otel-query
description: Query OpenTelemetry Demo telemetry data — Jaeger traces and Prometheus metrics — for APM analysis and troubleshooting.
triggers:
  - otel
  - OpenTelemetry
  - trace
  - span
  - metric
  - prometheus
  - jaeger
scripts:
  - name: query_traces
    description: Search Jaeger for traces by service name, operation, and lookback window.
    command: ["python", "scripts/query_traces.py"]
  - name: query_metrics
    description: Query Prometheus metrics via PromQL through Grafana proxy.
    command: ["python", "scripts/query_metrics.py"]
---
```

**query_traces.py:** 通过 HTTP 调 Jaeger API，输出 JSON traces。
**query_metrics.py:** 通过 Grafana 代理调 Prometheus API，输出 JSON metrics。

**TDD 步骤:**
1. RED: 写测试，mock HTTP 响应验证脚本行为
2. GREEN: 实现脚本
3. REFACTOR: 提取公共 HTTP 客户端逻辑

---

## Task 3: 配置集成

**文件:** `backend/.env` (worktree)

新增 OTEL demo 服务地址配置：
```ini
OTEL_JAEGER_API_URL=
OTEL_PROMETHEUS_PROXY_URL=
```

在 `.env.example` 中添加对应配置项，在 `config.py` 中添加对应 Settings 字段。

**TDD 步骤:**
1. RED: 写测试验证配置默认值和环境变量覆盖
2. GREEN: 添加 Settings 字段
3. REFACTOR: 无

---

## Task 4: 端到端集成测试

**文件:** `backend/tests/test_otel_query.py`

端到端测试验证完整数据流：
1. query_traces → from_jaeger_trace → build_observability_snapshot
2. query_metrics → from_prometheus_metric → 合并到 snapshot

**TDD 步骤:**
1. RED: 用 fixture 数据写端到端测试
2. GREEN: 确保完整流程通过
3. REFACTOR: 简化

---

## 执行顺序

Task 1 → Task 2 → Task 3 → Task 4 (严格串行，每一步依赖前一步)
