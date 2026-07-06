# -*- coding: utf-8 -*-
"""
Feishu (飞书) webhook alert push notifier for OTEL alerts.

Sends a brief alert card on P0/P1 trigger, and a full RCA result card
after root cause analysis completes. P2/P3 alerts are not pushed.

Reference implementation pattern:
  daily_stock_analysis/src/notification_sender/feishu_sender.py
  (Mode 1: Group Robot Webhook)
"""
import base64
import hashlib
import hmac
import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_WEBHOOK_SEND_TIMEOUT_SECONDS = 30


def _build_signature(secret: Optional[str]) -> dict:
    """Build HMAC-SHA256 signature fields for Feishu webhook security.

    Args:
        secret: The webhook signing secret. Empty/None skips signing.

    Returns:
        Dict with ``timestamp`` and ``sign`` keys for the POST payload.
    """
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
    """Build a brief Feishu interactive card for the initial alert notification.

    Card format follows the Feishu bot interactive card spec with
    ``lark_md`` rendering in a ``div`` element.
    """
    level = alert_data.get("level", "P0")
    alert_name = alert_data.get("alert_name", "Unknown")
    service_name = alert_data.get("service_name", "unknown")
    summary = alert_data.get("summary", "")
    severity = alert_data.get("severity", "")

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


def _build_rca_card(
    alert_data: dict,
    rca_result: Optional[str],
    status: str = "completed",
) -> dict:
    """Build a Feishu interactive card for the RCA result notification.

    Args:
        alert_data: The original alert dict with level, alert_name, etc.
        rca_result: The RCA analysis result text, or None if not available.
        status: One of ``completed``, ``failed``, ``blocked``, ``running``.

    Returns:
        Feishu interactive card payload dict.
    """
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

    When ``FEISHU_WEBHOOK_URL`` is not configured, all send methods are
    silent no-ops that return ``False``.

    Usage::

        notifier = FeishuNotifier()
        if notifier.enabled:
            notifier.send_alert(alert_data)
    """

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        webhook_secret: Optional[str] = None,
    ):
        self._webhook_url = (
            webhook_url or os.getenv("FEISHU_WEBHOOK_URL", "")
        ).strip()
        self._webhook_secret = (
            webhook_secret or os.getenv("FEISHU_WEBHOOK_SECRET", "")
        ).strip()
        self._verify_ssl = (
            os.getenv("WEBHOOK_VERIFY_SSL", "true").strip().lower()
            not in ("false", "0", "no")
        )

    @property
    def enabled(self) -> bool:
        """True when the webhook URL is configured."""
        return bool(self._webhook_url)

    def send_alert(self, alert_data: dict) -> bool:
        """Push brief alert notification to Feishu for P0/P1 alerts.

        Called immediately when a P0 or P1 alert is received.
        P2/P3 alerts should be filtered by the caller.
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

        Called after the RCA background task completes (or fails).
        """
        if not self.enabled:
            logger.debug("Feishu webhook not configured, skipping RCA push")
            return False

        card = _build_rca_card(alert_data, rca_result, status)
        return self._post_card(card)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post_card(self, card: dict) -> bool:
        """Post a Feishu interactive card, with plain-text fallback on failure."""
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
                    logger.error(
                        "Feishu webhook non-JSON response: %s", response.text[:200]
                    )
                    return False
                if not isinstance(result, dict):
                    logger.error(
                        "Feishu webhook unexpected format: %s", type(result).__name__
                    )
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


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_feishu_notifier: Optional[FeishuNotifier] = None


def get_feishu_notifier() -> FeishuNotifier:
    """Return the module-level :class:`FeishuNotifier` singleton.

    The singleton is lazily created on first access and reads configuration
    from environment variables.
    """
    global _feishu_notifier
    if _feishu_notifier is None:
        _feishu_notifier = FeishuNotifier()
    return _feishu_notifier
