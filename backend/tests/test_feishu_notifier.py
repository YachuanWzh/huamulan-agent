"""Tests for feishu_notifier module."""
from unittest.mock import MagicMock, patch

from personal_assistant.api.feishu_notifier import (
    FeishuNotifier,
    _build_alert_card,
    _build_rca_card,
    _build_signature,
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
        assert "失败" in content


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
