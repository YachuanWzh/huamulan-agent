# Feishu Alert Push Notifications — Implementation Plan

> **For agentic workers:** Execute this plan task-by-task under the superharness:go workflow, Phase 2 (strict TDD per task).

**Goal:** Add Feishu (飞书) webhook push notifications for P0/P1 OTEL alerts — send a brief alert message immediately on trigger, then push the full RCA analysis after completion. P2/P3 alerts are not pushed.

**Architecture:** A standalone `feishu_notifier.py`: HTTP POST to `FEISHU_WEBHOOK_URL` with optional HMAC-SHA256 signing. No SDK dependency. Integrated at two points in `server.py`: (1) `handle_otel_alert()` pushes brief message for P0/P1, (2) `_trigger_rca_background()` pushes full RCA result on completion.

**Tech Stack:** Python, `requests` (already available), FastAPI, no new dependencies.

---

### Task 1: Update .env.example with Feishu config keys

**Files:**
- Modify: `backend/.env.example`

- [ ] **Step 1: Add Feishu config section to .env.example**

```python
# ---- Feishu (飞书) Alert Push Notifications (optional) ------------------------
# Webhook URL for the Feishu group robot. When configured, P0 and P1 alerts
# are pushed to the Feishu group. P2/P3 alerts are NOT pushed.
#
# Get the webhook URL from: Feishu group → Settings → Group Bot → Add Bot →
# Custom Bot → Copy Webhook URL
#
# If the bot has "signature verification" enabled, also set FEISHU_WEBHOOK_SECRET.
#
# Required: no (omitting disables Feishu push)
# FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxxxx
# FEISHU_WEBHOOK_SECRET=your_webhook_secret
```

- [ ] **Step 2: Commit**

```bash
git add backend/.env.example
git commit -m "chore(otel): add Feishu webhook config keys to .env.example"
```

---

### Task 2: Create feishu_notifier.py module

**Files:**
- Create: `backend/src/personal_assistant/api/feishu_notifier.py`
- Create: `backend/tests/test_feishu_notifier.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for feishu_notifier module."""
import json
import time
from unittest.mock import patch, MagicMock
import pytest
from personal_assistant.api.feishu_notifier import (
    FeishuNotifier,
    _build_signature,
    _build_alert_card,
    _build_rca_card,
)


class TestBuildSignature:
    def test_build_signature_returns_timestamp_and_sign(self):
        secret = "test-secret"
        result = _build_signature(secret)
        assert "timestamp" in result
        assert "sign" in result
        assert result["timestamp"].isdigit()

    def test_build_signature_empty_secret_returns_empty_dict(self):
        assert _build_signature("") == {}
        assert _build_signature(None) == {}


class TestBuildAlertCard:
    def test_build_alert_card_p0(self):
        alert_data = {
            "level": "P0",
            "alert_name": "ServiceDown",
            "service_name": "frontend",
            "summary": "Service frontend is DOWN",
            "severity": "critical",
        }
        card = _build_alert_card(alert_data)
        assert card["msg_type"] == "interactive"
        header_title = card["card"]["header"]["title"]["content"]
        assert "P0" in header_title
        element_text = card["card"]["elements"][0]["text"]["content"]
        assert "P0" in element_text
        assert "Service frontend is DOWN" in element_text

    def test_build_alert_card_p1(self):
        alert_data = {
            "level": "P1",
            "alert_name": "HighLatency",
            "service_name": "checkout",
            "summary": "High latency detected",
            "severity": "warning",
        }
        card = _build_alert_card(alert_data)
        assert "P1" in card["card"]["header"]["title"]["content"]
        assert "High latency detected" in card["card"]["elements"][0]["text"]["content"]


class TestBuildRcaCard:
    def test_build_rca_card_with_result(self):
        alert_data = {
            "level": "P0",
            "alert_name": "ServiceDown",
            "service_name": "frontend",
            "summary": "Service frontend is DOWN",
            "severity": "critical",
        }
        rca_result = "Root cause: Network partition in zone us-east-1."
        card = _build_rca_card(alert_data, rca_result)
        assert card["msg_type"] == "interactive"
        header = card["card"]["header"]["title"]["content"]
        assert "RCA" in header
        content = card["card"]["elements"][0]["text"]["content"]
        assert "Network partition" in content
        assert "Service frontend is DOWN" in content

    def test_build_rca_card_failed(self):
        alert_data = {
            "level": "P1",
            "alert_name": "HighLatency",
            "service_name": "checkout",
            "summary": "High latency detected",
            "severity": "warning",
        }
        card = _build_rca_card(alert_data, rca_result=None, status="failed")
        content = card["card"]["elements"][0]["text"]["content"]
        assert "failed" in content.lower() or "失败" in content


class TestFeishuNotifier:
    def test_init_without_url_disables_push(self):
        notifier = FeishuNotifier()
        assert notifier.enabled is False

    def test_init_with_url_enables_push(self):
        notifier = FeishuNotifier(webhook_url="https://open.feishu.cn/hook/test")
        assert notifier.enabled is True

    def test_send_alert_skips_when_disabled(self):
        notifier = FeishuNotifier()
        result = notifier.send_alert({"level": "P0", "summary": "test"})
        assert result is False

    @patch("personal_assistant.api.feishu_notifier.requests.post")
    def test_send_alert_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": 0, "msg": "ok"}
        mock_post.return_value = mock_response

        notifier = FeishuNotifier(webhook_url="https://open.feishu.cn/hook/test")
        result = notifier.send_alert({
            "level": "P0",
            "alert_name": "TestAlert",
            "service_name": "test-svc",
            "summary": "Test alert",
            "severity": "critical",
        })
        assert result is True
        mock_post.assert_called_once()

    @patch("personal_assistant.api.feishu_notifier.requests.post")
    def test_send_alert_with_signature(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": 0}
        mock_post.return_value = mock_response

        notifier = FeishuNotifier(
            webhook_url="https://open.feishu.cn/hook/test",
            webhook_secret="my-secret",
        )
        result = notifier.send_alert({
            "level": "P0",
            "alert_name": "TestAlert",
            "service_name": "test-svc",
            "summary": "Test alert",
            "severity": "critical",
        })
        assert result is True
        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert "timestamp" in payload
        assert "sign" in payload

    @patch("personal_assistant.api.feishu_notifier.requests.post")
    def test_send_rca_result_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": 0}
        mock_post.return_value = mock_response

        notifier = FeishuNotifier(webhook_url="https://open.feishu.cn/hook/test")
        result = notifier.send_rca_result(
            {
                "level": "P0",
                "alert_name": "TestAlert",
                "service_name": "test-svc",
                "summary": "Test alert",
                "severity": "critical",
            },
            rca_result="Root cause analysis complete.",
            status="completed",
        )
        assert result is True

    def test_send_rca_skips_when_disabled(self):
        notifier = FeishuNotifier()
        result = notifier.send_rca_result({"level": "P0"}, "result", "completed")
        assert result is False
```

- [ ] **Step 2: Run tests, verify RED**

```
cd backend && python -m pytest tests/test_feishu_notifier.py -v
```

Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal feishu_notifier.py**

```python
# -*- coding: utf-8 -*-
"""
Feishu (飞书) webhook alert push notifier for OTEL alerts.

Sends brief alert card on P0/P1 trigger, and full RCA result card
after root cause analysis completes. P2/P3 alerts are not pushed.

"""
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

_WEBHOOK_SEND_TIMEOUT_SECONDS = 30


def _build_signature(secret: Optional[str]) -> dict:
    """Build HMAC-SHA256 signature fields for Feishu webhook security."""
    if not secret or not secret.strip():
        return {}
    secret = secret.strip()
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}"
    sign = base64.b64encode(
        hmac.new(
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    return {"timestamp": timestamp, "sign": sign}


def _build_alert_card(alert_data: dict) -> dict:
    """Build a brief Feishu interactive card for the initial alert notification."""
    level = alert_data.get("level", "P0")
    alert_name = alert_data.get("alert_name", "Unknown")
    service_name = alert_data.get("service_name", "unknown")
    summary = alert_data.get("summary", "")
    severity = alert_data.get("severity", "")

    # Build the body content — simple text format as specified:
    # "告警等级：p0\n告警内容：xxx"
    body_lines = [
        f"告警等级：{level}",
        f"告警名称：{alert_name}",
        f"告警服务：{service_name}",
        f"严重程度：{severity}",
        f"告警内容：{summary}",
    ]
    body_text = "\n".join(body_lines)

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"🚨 {level} 告警触发"},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": body_text},
                }
            ],
        },
    }


def _build_rca_card(alert_data: dict, rca_result: Optional[str], status: str = "completed") -> dict:
    """Build a Feishu interactive card for the RCA result notification."""
    level = alert_data.get("level", "P0")
    alert_name = alert_data.get("alert_name", "Unknown")
    service_name = alert_data.get("service_name", "unknown")
    summary = alert_data.get("summary", "")

    if status == "completed" and rca_result:
        status_label = "✅ RCA 分析完成"
        result_text = rca_result
    elif status == "failed":
        status_label = "❌ RCA 分析失败"
        result_text = "根因分析执行失败，请检查系统日志。"
    elif status == "blocked":
        status_label = "⏸️ RCA 等待审批"
        result_text = "根因分析需要人工审批后才能继续。"
    else:
        status_label = "⏳ RCA 分析中"
        result_text = rca_result or "分析进行中..."

    body_lines = [
        f"告警等级：{level}",
        f"告警名称：{alert_name}",
        f"告警服务：{service_name}",
        f"告警内容：{summary}",
        "",
        f"**{status_label}**",
        "",
        result_text,
    ]
    body_text = "\n".join(body_lines)

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"🔍 {level} RCA 分析结果"},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": body_text},
                }
            ],
        },
    }


class FeishuNotifier:
    """Send OTEL alert notifications to Feishu via webhook.

    When FEISHU_WEBHOOK_URL is not configured, all send methods are
    silent no-ops that return False.
    """

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        webhook_secret: Optional[str] = None,
    ):
        self._webhook_url = (webhook_url or os.getenv("FEISHU_WEBHOOK_URL", "")).strip()
        self._webhook_secret = (webhook_secret or os.getenv("FEISHU_WEBHOOK_SECRET", "")).strip()
        self._verify_ssl = os.getenv("WEBHOOK_VERIFY_SSL", "true").strip().lower() not in ("false", "0", "no")

    @property
    def enabled(self) -> bool:
        return bool(self._webhook_url)

    def send_alert(self, alert_data: dict) -> bool:
        """Push brief alert notification to Feishu for P0/P1 alerts.

        Called immediately when a P0 or P1 alert is received.
        P2/P3 alerts are NOT pushed — the caller should filter before calling.
        """
        if not self.enabled:
            logger.debug("Feishu webhook not configured, skipping alert push")
            return False

        card = _build_alert_card(alert_data)
        return self._post_card(card)

    def send_rca_result(
        self,
        alert_data: dict,
        rca_result: Optional[str],
        status: str = "completed",
    ) -> bool:
        """Push full RCA analysis result to Feishu.

        Called after RCA background task completes (or fails).
        """
        if not self.enabled:
            logger.debug("Feishu webhook not configured, skipping RCA push")
            return False

        card = _build_rca_card(alert_data, rca_result, status)
        return self._post_card(card)

    def _post_card(self, card: dict) -> bool:
        """Post a Feishu interactive card with text fallback."""
        security_fields = _build_signature(self._webhook_secret)

        def _post(payload: dict) -> bool:
            request_payload = dict(payload)
            request_payload.update(security_fields)
            try:
                response = requests.post(
                    self._webhook_url,
                    json=request_payload,
                    timeout=_WEBHOOK_SEND_TIMEOUT_SECONDS,
                    verify=self._verify_ssl,
                )
            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.RequestException,
            ) as e:
                logger.error("Feishu webhook network error: %s", e)
                return False

            if response.status_code == 200:
                try:
                    result = response.json()
                except (ValueError, AttributeError):
                    logger.error("Feishu webhook non-JSON response: %s", response.text[:200])
                    return False
                if not isinstance(result, dict):
                    logger.error("Feishu webhook unexpected format: %s", type(result).__name__)
                    return False
                code = result.get("code") if "code" in result else result.get("StatusCode")
                if code == 0:
                    logger.info("Feishu webhook push succeeded")
                    return True
                logger.error(
                    "Feishu webhook error [code=%s]: %s",
                    code,
                    result.get("msg") or result.get("StatusMessage", "unknown error"),
                )
                return False

            logger.error("Feishu webhook HTTP %d", response.status_code)
            return False

        # Try interactive card first
        if _post(card):
            return True

        # Fallback to plain text
        try:
            text_content = card["card"]["elements"][0]["text"]["content"]
        except (KeyError, IndexError):
            text_content = "Alert notification"
        text_payload = {
            "msg_type": "text",
            "content": {"text": text_content},
        }
        return _post(text_payload)


# Module-level singleton for convenience
_feishu_notifier: Optional[FeishuNotifier] = None


def get_feishu_notifier() -> FeishuNotifier:
    """Return the module-level FeishuNotifier singleton."""
    global _feishu_notifier
    if _feishu_notifier is None:
        _feishu_notifier = FeishuNotifier()
    return _feishu_notifier
```

- [ ] **Step 4: Run tests, verify GREEN**

```
cd backend && python -m pytest tests/test_feishu_notifier.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/api/feishu_notifier.py backend/tests/test_feishu_notifier.py
git commit -m "feat(otel): add Feishu webhook notifier for P0/P1 alert push"
```

---

### Task 3: Integrate Feishu pushes into server.py

**Files:**
- Modify: `backend/src/personal_assistant/api/server.py`

- [ ] **Step 1: Update existing webhook test to verify Feishu calls**

Add to `backend/tests/test_otel_webhook.py`:

```python
# Add at top of file, after imports:
from unittest.mock import patch

# Add this test method:
class TestFeishuIntegration:
    """Test that Feishu notifier is called for P0/P1 but not P2/P3."""

    @patch("personal_assistant.api.server.get_feishu_notifier")
    def test_p0_alert_triggers_feishu_push(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_notifier.enabled = True
        mock_notifier.send_alert.return_value = True
        mock_get_notifier.return_value = mock_notifier

        response = client.post("/api/otel/alerts", json=ALERTMANAGER_P0_PAYLOAD)
        assert response.status_code == 200
        mock_notifier.send_alert.assert_called_once()
        call_args = mock_notifier.send_alert.call_args[0][0]
        assert call_args["level"] == "P0"

    @patch("personal_assistant.api.server.get_feishu_notifier")
    def test_p1_alert_triggers_feishu_push(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_notifier.enabled = True
        mock_notifier.send_alert.return_value = True
        mock_get_notifier.return_value = mock_notifier

        response = client.post("/api/otel/alerts", json=ALERTMANAGER_P1_PAYLOAD)
        assert response.status_code == 200
        mock_notifier.send_alert.assert_called_once()

    @patch("personal_assistant.api.server.get_feishu_notifier")
    def test_p2_alert_does_not_trigger_feishu_push(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_notifier.enabled = True
        mock_get_notifier.return_value = mock_notifier

        response = client.post("/api/otel/alerts", json=ALERTMANAGER_P2_PAYLOAD)
        assert response.status_code == 200
        mock_notifier.send_alert.assert_not_called()

    @patch("personal_assistant.api.server.get_feishu_notifier")
    def test_p3_alert_does_not_trigger_feishu_push(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_notifier.enabled = True
        mock_get_notifier.return_value = mock_notifier

        response = client.post("/api/otel/alerts", json=ALERTMANAGER_P3_PAYLOAD)
        assert response.status_code == 200
        mock_notifier.send_alert.assert_not_called()

    @patch("personal_assistant.api.server.get_feishu_notifier")
    def test_feishu_disabled_skips_push(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_notifier.enabled = False
        mock_get_notifier.return_value = mock_notifier

        response = client.post("/api/otel/alerts", json=ALERTMANAGER_P0_PAYLOAD)
        assert response.status_code == 200
        mock_notifier.send_alert.assert_not_called()
```

- [ ] **Step 2: Run tests, verify RED (no integration yet)**

```
cd backend && python -m pytest tests/test_otel_webhook.py -v -k "Feishu"
```

Expected: FAIL (mock not called)

- [ ] **Step 3: Modify server.py to integrate Feishu pushes**

In `handle_otel_alert()`, add Feishu push for P0/P1 after alert is stored:

```python
# After line 557 (await _broadcast_otel_alert(alert_data)):
        # Push brief alert to Feishu for P0/P1
        if level in ("P0", "P1"):
            _ = get_feishu_notifier().send_alert(alert_data)
```

In `_trigger_rca_background()`, add Feishu push after RCA completes:

```python
# After the try/except block at the end of _trigger_rca_background(),
# before the finally block, or replace the finally block:

    finally:
        await _broadcast_otel_alert(alert_data)
        # Push RCA result to Feishu for P0/P1
        if alert_data.get("level") in ("P0", "P1"):
            rca_status = alert_data.get("rca_status", "unknown")
            rca_result_text = None
            if response.status == "completed":  # only available for success case
                # Extract the agent's response text
                rca_result_text = _extract_rca_result_text(response)
            _ = get_feishu_notifier().send_rca_result(
                alert_data, rca_result_text, rca_status
            )
```

Wait — the `response` variable is scoped inside `try`. Let me think about this more carefully.

The RCA result text extraction needs careful handling. The `response` is only available in the try block. Let me restructure:

```python
async def _trigger_rca_background(alert_data: dict) -> None:
    from personal_assistant.agent.harness import requires_rca_tool_approval

    alert_id = alert_data["id"]
    thread_id = f"rca-{alert_id}"
    _update_alert_rca_status(
        alert_data,
        rca_status="running",
        rca_thread_id=thread_id,
    )
    await _broadcast_otel_alert(alert_data)

    rca_result_text = None
    try:
        prompt = _build_rca_prompt(alert_data)
        response = await harness.run_user_turn(
            thread_id,
            prompt,
            agent_mode="single",
            requires_approval=requires_rca_tool_approval,
        )

        if response.status == "requires_approval":
            _update_alert_rca_status(
                alert_data,
                rca_status="blocked",
                rca_pending_approvals=[
                    a.model_dump() for a in response.approvals
                ],
            )
        else:
            _update_alert_rca_status(
                alert_data,
                rca_status="completed",
                rca_pending_approvals=None,
            )
            # Extract RCA result text from agent response
            rca_result_text = _extract_rca_result_text(response)
    except Exception:
        logger.exception("RCA failed for alert %s", alert_id)
        _update_alert_rca_status(
            alert_data,
            rca_status="failed",
            rca_pending_approvals=None,
        )
    finally:
        await _broadcast_otel_alert(alert_data)
        # Push RCA result to Feishu for P0/P1
        if alert_data.get("level") in ("P0", "P1"):
            _ = get_feishu_notifier().send_rca_result(
                alert_data,
                rca_result=rca_result_text,
                status=alert_data.get("rca_status", "failed"),
            )
```

And add a helper `_extract_rca_result_text()`:

```python
def _extract_rca_result_text(response) -> Optional[str]:
    """Extract the RCA agent's final text response."""
    try:
        messages = getattr(response, "messages", None)
        if messages and hasattr(messages, "__iter__"):
            # Get the last AI message text
            for msg in reversed(list(messages)):
                if hasattr(msg, "content") and msg.content:
                    if hasattr(msg, "type") and msg.type == "ai":
                        return str(msg.content)
                    # Fallback: any message with content
                    return str(msg.content)
    except Exception:
        logger.debug("Could not extract RCA result text", exc_info=True)
    return None
```

- [ ] **Step 4: Run tests, verify GREEN**

```
cd backend && python -m pytest tests/test_otel_webhook.py -v -k "Feishu"
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/api/server.py backend/tests/test_otel_webhook.py
git commit -m "feat(otel): integrate Feishu push for P0/P1 alert trigger and RCA completion"
```

---

### Task 4: Full test suite verification

- [ ] **Step 1: Run full test suite**

```
cd backend && python -m pytest tests/test_otel_webhook.py tests/test_feishu_notifier.py -v
```

Expected: all PASS

- [ ] **Step 2: Run broader test suite**

```
cd backend && python -m pytest tests/ -v --timeout=60 -x
```

Expected: all existing tests still PASS
