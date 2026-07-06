# -*- coding: utf-8 -*-
"""Tests for Feishu Stream Mode bidirectional communication."""

import json
from unittest.mock import MagicMock, patch

import pytest

from personal_assistant.api.feishu_stream import (
    FeishuReplyClient,
    FeishuStreamClient,
    FeishuStreamHandler,
    _build_reply_card,
    _chunk_content,
    _extract_command_text,
    _parse_event_to_message,
    FEISHU_SDK_AVAILABLE,
)


# ── _extract_command_text tests ────────────────────────────────────


class TestExtractCommandText:
    """Tests for _extract_command_text which strips @bot mentions."""

    def test_removes_at_user_mentions(self):
        text = "@_user_1 帮我分析一下这个告警"
        result = _extract_command_text(text, ["@_user_1"])
        assert result == "帮我分析一下这个告警"

    def test_removes_multiple_mentions(self):
        text = "@_user_1 @_user_2 查询系统状态"
        result = _extract_command_text(text, ["@_user_1", "@_user_2"])
        assert result == "查询系统状态"

    def test_handles_no_mentions(self):
        text = "查询系统状态"
        result = _extract_command_text(text, [])
        assert result == "查询系统状态"

    def test_handles_empty_text(self):
        result = _extract_command_text("", [])
        assert result == ""

    def test_fallback_regex_when_mentions_empty(self):
        text = "@_user_1 你好"
        result = _extract_command_text(text, [])
        assert result == "你好"

    def test_handles_regex_only_mentions(self):
        text = "@_user_1 @_user_2"
        result = _extract_command_text(text, [])
        assert result == ""


# ── _build_reply_card tests ────────────────────────────────────────


class TestBuildReplyCard:
    """Tests for _build_reply_card which creates interactive card payloads."""

    def test_builds_basic_card(self):
        card = _build_reply_card("Hello Feishu")
        assert card["msg_type"] == "interactive"
        assert card["card"]["config"]["wide_screen_mode"] is True
        elements = card["card"]["elements"]
        assert len(elements) == 1
        assert elements[0]["tag"] == "div"
        assert elements[0]["text"]["tag"] == "lark_md"
        assert elements[0]["text"]["content"] == "Hello Feishu"

    def test_builds_card_with_header(self):
        card = _build_reply_card("Result", header="分析结果")
        assert card["card"]["header"]["title"]["tag"] == "plain_text"
        assert card["card"]["header"]["title"]["content"] == "分析结果"

    def test_builds_card_without_header(self):
        card = _build_reply_card("Plain text only")
        assert "header" not in card["card"]


# ── _chunk_content tests ──────────────────────────────────────────


class TestChunkContent:
    """Tests for _chunk_content which splits long messages."""

    def test_short_content_not_chunked(self):
        chunks = _chunk_content("Short message", max_bytes=20000)
        assert chunks == ["Short message"]

    def test_long_content_chunked(self):
        long_text = "A" * 100
        chunks = _chunk_content(long_text, max_bytes=10)
        assert len(chunks) > 1
        assert all(len(c.encode("utf-8")) <= 10 for c in chunks)

    def test_chunks_preserve_content(self):
        text = "0123456789" * 100
        chunks = _chunk_content(text, max_bytes=500)
        assert "".join(chunks) == text

    def test_multi_byte_utf8_boundaries(self):
        text = "中文测试内容" * 50
        chunks = _chunk_content(text, max_bytes=200)
        assert "".join(chunks) == text
        assert all(len(c.encode("utf-8")) <= 200 for c in chunks)


# ── _parse_event_to_message tests ──────────────────────────────────


class TestParseEventToMessage:
    """Tests for _parse_event_to_message converting Feishu events."""

    def test_parses_text_message(self):
        event_dict = {
            "header": {"event_id": "evt_001", "event_type": "im.message.receive_v1"},
            "event": {
                "message": {
                    "message_id": "msg_001",
                    "chat_id": "oc_abc123",
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": json.dumps({"text": "你好"}),
                    "create_time": "1700000000000",
                },
                "sender": {
                    "sender_id": {"open_id": "ou_xyz789", "user_id": "uid_001"}
                },
            },
        }
        msg = _parse_event_to_message(event_dict)
        assert msg is not None
        assert msg["message_id"] == "msg_001"
        assert msg["chat_id"] == "oc_abc123"
        assert msg["chat_type"] == "private"
        assert msg["content"] == "你好"

    def test_parses_group_message(self):
        event_dict = {
            "header": {"event_id": "evt_002"},
            "event": {
                "message": {
                    "message_id": "msg_002",
                    "chat_id": "oc_group_1",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": json.dumps({"text": "早上好"}),
                    "mentions": [{"key": "@_user_1", "name": "Bot"}],
                },
                "sender": {
                    "sender_id": {"open_id": "ou_user_1"}
                },
            },
        }
        msg = _parse_event_to_message(event_dict)
        assert msg is not None
        assert msg["chat_type"] == "group"
        assert msg["content"] == "早上好"

    def test_ignores_non_text_messages(self):
        event_dict = {
            "header": {"event_id": "evt_003"},
            "event": {
                "message": {
                    "message_id": "msg_003",
                    "chat_id": "oc_abc123",
                    "chat_type": "p2p",
                    "message_type": "image",
                    "content": json.dumps({"image_key": "xxx"}),
                },
                "sender": {
                    "sender_id": {"open_id": "ou_xyz789"}
                },
            },
        }
        msg = _parse_event_to_message(event_dict)
        assert msg is None

    def test_handles_missing_message(self):
        event_dict = {
            "header": {"event_id": "evt_004"},
            "event": {},
        }
        msg = _parse_event_to_message(event_dict)
        assert msg is None

    def test_handles_invalid_json_content(self):
        event_dict = {
            "header": {"event_id": "evt_005"},
            "event": {
                "message": {
                    "message_id": "msg_005",
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": "not valid json",
                },
                "sender": {
                    "sender_id": {"open_id": "ou_test"}
                },
            },
        }
        msg = _parse_event_to_message(event_dict)
        assert msg is not None
        assert msg["content"] == "not valid json"


# ── FeishuReplyClient tests ────────────────────────────────────────


class TestFeishuReplyClient:
    """Tests for FeishuReplyClient (mocked SDK)."""

    @patch("personal_assistant.api.feishu_stream.FEISHU_SDK_AVAILABLE", True)
    @patch("personal_assistant.api.feishu_stream.lark")
    def test_init_builds_client(self, mock_lark):
        client = FeishuReplyClient(app_id="test_id", app_secret="test_secret")
        mock_lark.Client.builder.assert_called_once()
        assert client._app_id == "test_id"

    @patch("personal_assistant.api.feishu_stream.FEISHU_SDK_AVAILABLE", False)
    def test_init_raises_when_sdk_missing(self):
        with pytest.raises(ImportError, match="lark-oapi"):
            FeishuReplyClient(app_id="test_id", app_secret="test_secret")

    @patch("personal_assistant.api.feishu_stream.FEISHU_SDK_AVAILABLE", True)
    @patch("personal_assistant.api.feishu_stream.lark")
    def test_reply_message_success(self, mock_lark):
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_client = MagicMock()
        mock_client.im.v1.message.reply.return_value = mock_response
        mock_lark.Client.builder.return_value.build.return_value = mock_client

        client = FeishuReplyClient(app_id="test_id", app_secret="test_secret")
        client._client = mock_client
        result = client.reply_text(message_id="msg_001", text="Hello!")

        assert result is True

    @patch("personal_assistant.api.feishu_stream.FEISHU_SDK_AVAILABLE", True)
    @patch("personal_assistant.api.feishu_stream.lark")
    def test_reply_message_failure(self, mock_lark):
        mock_response = MagicMock()
        mock_response.success.return_value = False
        mock_response.code = 10001
        mock_response.msg = "Error"
        mock_client = MagicMock()
        mock_client.im.v1.message.reply.return_value = mock_response
        mock_lark.Client.builder.return_value.build.return_value = mock_client

        client = FeishuReplyClient(app_id="test_id", app_secret="test_secret")
        client._client = mock_client
        result = client.reply_text(message_id="msg_001", text="Hello!")

        assert result is False


# ── FeishuStreamHandler integration tests ──────────────────────────


class TestFeishuStreamHandler:
    """Tests for the message handler FIFO processing."""

    def test_processes_message_and_replies(self):
        received = []
        reply_client = MagicMock()
        reply_client.reply_text.return_value = True

        def on_message(msg):
            received.append(msg)
            return f"Reply to: {msg['content']}"

        handler = FeishuStreamHandler(
            on_message=on_message,
            reply_client=reply_client,
        )

        msg = {
            "message_id": "msg_001",
            "chat_id": "oc_test",
            "chat_type": "private",
            "user_id": "ou_test",
            "content": "Hello",
            "raw_content": "Hello",
            "mentioned": False,
            "timestamp": "1700000000000",
        }

        handler._enqueue(msg)
        handler.shutdown(wait=True)

        assert len(received) == 1
        assert received[0]["content"] == "Hello"
        reply_client.reply_text.assert_called_once()

    def test_empty_reply_skipped(self):
        reply_client = MagicMock()

        def on_message(msg):
            return None

        handler = FeishuStreamHandler(
            on_message=on_message,
            reply_client=reply_client,
        )

        msg = {
            "message_id": "msg_002",
            "chat_id": "oc_test",
            "chat_type": "private",
            "user_id": "ou_test",
            "content": "",
            "raw_content": "",
            "mentioned": False,
            "timestamp": "1700000000000",
        }

        handler._enqueue(msg)
        handler.shutdown(wait=True)

        reply_client.reply_text.assert_not_called()

    def test_shutdown_stops_processing(self):
        reply_client = MagicMock()
        handler = FeishuStreamHandler(
            on_message=lambda m: "reply",
            reply_client=reply_client,
        )
        handler.shutdown(wait=True)

        msg = {"message_id": "msg_003", "content": "after shutdown"}
        handler._enqueue(msg)
        handler.shutdown(wait=True)

        reply_client.reply_text.assert_not_called()


# ── FeishuStreamClient tests ───────────────────────────────────────


class TestFeishuStreamClientConfig:
    """Tests for FeishuStreamClient configuration modes."""

    @patch("personal_assistant.api.feishu_stream.FEISHU_SDK_AVAILABLE", True)
    def test_enabled_when_credentials_set(self):
        client = FeishuStreamClient(
            on_message=lambda m: None,
            app_id="test_id",
            app_secret="test_secret",
        )
        assert client.enabled is True

    @patch("personal_assistant.api.feishu_stream.FEISHU_SDK_AVAILABLE", True)
    def test_disabled_when_no_credentials(self):
        client = FeishuStreamClient(
            on_message=lambda m: None,
            app_id="",
            app_secret="",
        )
        assert client.enabled is False

    @patch("personal_assistant.api.feishu_stream.FEISHU_SDK_AVAILABLE", True)
    def test_start_background_skips_when_disabled(self):
        client = FeishuStreamClient(
            on_message=lambda m: None,
            app_id="",
            app_secret="",
        )
        client.start_background()
        assert client.running is False
