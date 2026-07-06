# Feishu Bidirectional Communication Implementation Plan

> **For agentic workers:** Execute this plan task-by-task under the superharness:go workflow, Phase 2 (strict TDD per task). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable bidirectional Feishu communication — receive user messages from Feishu via Stream Mode (WebSocket) and route them to the LangGraph agent for processing, then reply with agent responses back to the Feishu conversation.

**Architecture:** Use the `lark-oapi` SDK's WebSocket Stream Mode (same as reference project) to receive `im.message.receive_v1` events without needing a public URL. Incoming messages are parsed, routed to `AgentHarness.run_user_turn()` for AI processing, and responses are sent back via Feishu's `im.v1.message.reply` API as interactive cards. A new `FeishuStreamClient` manages the WebSocket connection lifecycle, started alongside the FastAPI server via the lifespan manager.

**Tech Stack:** `lark-oapi>=1.0.0` (Feishu official Python SDK), existing `AgentHarness` + `PostgresMemory` for agent processing, existing interactive card patterns from `FeishuNotifier`.

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `backend/src/personal_assistant/api/feishu_stream.py` | Stream client, reply client, message handler, event parsing |
| Create | `backend/tests/test_feishu_stream.py` | Unit + integration tests |
| Modify | `backend/pyproject.toml` | Add `lark-oapi` dependency |
| Modify | `backend/src/personal_assistant/config.py` | Add `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_STREAM_ENABLED` |
| Modify | `backend/src/personal_assistant/api/server.py` | Start/stop stream client in lifespan, add Feishu message callback |
| Modify | `backend/.env.example` | Document new Feishu config vars |
| Modify | `backend/src/personal_assistant/api/feishu_notifier.py` | Extract shared card-building helpers (optional refactor) |

---

### Task 1: Add `lark-oapi` dependency

**Files:**
- Modify: `backend/pyproject.toml:6-22`

- [ ] **Step 1: Add `lark-oapi` to dependencies**

In `backend/pyproject.toml`, add `"lark-oapi>=1.0.0"` to the `dependencies` list:

```toml
dependencies = [
  "fastapi>=0.115.0",
  "uvicorn[standard]>=0.30.0",
  "pydantic-settings>=2.4.0",
  "langchain-core>=0.3.0",
  "langchain-openai>=0.2.0",
  "langgraph>=0.2.0",
  "langgraph-checkpoint-postgres>=2.0.0",
  "psycopg[binary,pool]>=3.2.0",
  "watchfiles>=0.24.0",
  "pyyaml>=6.0",
  "langfuse>=3.0.0",
  "langchain-deepseek>=1.1.0",
  "redis>=5.0.0",
  "kafka-python>=2.0.0",
  "langchain>=1.3.11",
  "lark-oapi>=1.0.0",
]
```

- [ ] **Step 2: Install the dependency**

Run: `pip install lark-oapi>=1.0.0`

Expected: Package installs successfully, `import lark_oapi` works in Python.

- [ ] **Step 3: Commit**

```bash
git add backend/pyproject.toml
git commit -m "feat: add lark-oapi SDK dependency for Feishu Stream Mode

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Add Feishu Stream configuration

**Files:**
- Modify: `backend/src/personal_assistant/config.py:199-207`
- Modify: `backend/.env.example:167-186`

- [ ] **Step 1: Add new Feishu settings to config.py**

Replace the existing Feishu config block (lines 199-207) with:

```python
    # ── Feishu (飞书) ──────────────────────────────────────────────
    # Webhook mode (existing: agent → Feishu push)
    feishu_webhook_url: str = Field(
        default="",
        alias="FEISHU_WEBHOOK_URL",
    )
    feishu_webhook_secret: str = Field(
        default="",
        alias="FEISHU_WEBHOOK_SECRET",
    )
    # Stream mode (new: Feishu → agent bidirectional)
    feishu_app_id: str = Field(
        default="",
        alias="FEISHU_APP_ID",
    )
    feishu_app_secret: str = Field(
        default="",
        alias="FEISHU_APP_SECRET",
    )
    feishu_stream_enabled: bool = Field(
        default=False,
        alias="FEISHU_STREAM_ENABLED",
    )
```

- [ ] **Step 2: Verify config loads correctly**

Run:
```python
# In a Python shell at backend/
from personal_assistant.config import get_settings
s = get_settings()
print(s.feishu_app_id, s.feishu_app_secret, s.feishu_stream_enabled)
# Expected: "" "" False (with no env vars set)
```

- [ ] **Step 3: Document new settings in .env.example**

Append after the existing Feishu block (after line 186 in `.env.example`):

```ini
# ── Feishu (飞书) Stream Mode (bidirectional agent bot) ──────────────
# Stream Mode uses WebSocket long connection to receive messages from
# Feishu users and route them to the AI agent for processing. No
# public URL or webhook callback endpoint required.
#
# Setup steps:
# 1. Create a Feishu app at https://open.feishu.cn/app
# 2. Enable "Bot" capability in the app
# 3. Copy App ID and App Secret from "Credentials and Basic Info"
# 4. Set FEISHU_STREAM_ENABLED=true
# 5. Subscribe to im.message.receive_v1 event in the app console
#
#FEISHU_APP_ID=cli_xxxxxxxxxxxx
#FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
#FEISHU_STREAM_ENABLED=false
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/personal_assistant/config.py backend/.env.example
git commit -m "feat: add Feishu Stream Mode config (APP_ID, APP_SECRET, STREAM_ENABLED)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Write failing tests for FeishuStreamClient and reply client

**Files:**
- Create: `backend/tests/test_feishu_stream.py`

- [ ] **Step 1: Write the test file**

```python
# -*- coding: utf-8 -*-
"""Tests for Feishu Stream Mode bidirectional communication."""

import json
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from personal_assistant.api.feishu_stream import (
    FeishuReplyClient,
    FeishuStreamHandler,
    FeishuStreamClient,
    _parse_event_to_message,
    _extract_command_text,
    _build_reply_card,
    _chunk_content,
    FEISHU_SDK_AVAILABLE,
)


# ── Message parsing tests ────────────────────────────────────────


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

    def test_ignores_non_text_messages(self):
        event_dict = {
            "header": {"event_id": "evt_001", "event_type": "im.message.receive_v1"},
            "event": {
                "message": {
                    "message_id": "msg_002",
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

    def test_handles_missing_fields(self):
        event_dict = {
            "header": {"event_id": "evt_001"},
            "event": {},
        }
        msg = _parse_event_to_message(event_dict)
        assert msg is None


# ── FeishuReplyClient tests ───────────────────────────────────────


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_feishu_stream.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'personal_assistant.api.feishu_stream'`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_feishu_stream.py
git commit -m "test: add failing tests for Feishu Stream Mode module

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Implement Feishu Stream module

**Files:**
- Create: `backend/src/personal_assistant/api/feishu_stream.py`

- [ ] **Step 1: Create the module**

Write `backend/src/personal_assistant/api/feishu_stream.py`:

```python
# -*- coding: utf-8 -*-
"""
Feishu (飞书) Stream Mode bidirectional communication.

Uses the lark-oapi SDK's WebSocket long connection to receive messages
from Feishu users (no public URL required) and route them to the
LangGraph agent, then reply with agent responses.

Architecture:
  Feishu Server ←→ ws.Client (WebSocket) → EventDispatcherHandler
      → FeishuStreamHandler._enqueue_message (FIFO per conversation)
      → on_message callback → AgentHarness.run_user_turn()
      → FeishuReplyClient.reply_text() → im.v1.message.reply

Reference: daily_stock_analysis/bot/platforms/feishu_stream.py
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── Lazy SDK import ──────────────────────────────────────────────
try:
    import lark_oapi as lark
    from lark_oapi import ws as lark_ws
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
        P2ImMessageReceiveV1,
        ReplyMessageRequest,
        ReplyMessageRequestBody,
    )

    FEISHU_SDK_AVAILABLE = True
except ImportError:
    FEISHU_SDK_AVAILABLE = False
    lark = None  # type: ignore
    lark_ws = None  # type: ignore
    logger.warning(
        "[Feishu Stream] lark-oapi SDK not installed. "
        "Stream Mode unavailable. Run: pip install lark-oapi"
    )

# ── Constants ────────────────────────────────────────────────────

_DEFAULT_MAX_BYTES = 20000
_PAGE_MARKER_TEMPLATE = "\n\n---\n*(第 {current}/{total} 页)*"

# ── Public data types ────────────────────────────────────────────


@dataclass(frozen=True)
class FeishuMessage:
    """Parsed incoming Feishu message in unified format."""

    message_id: str
    chat_id: str
    chat_type: str  # "private" | "group" | "unknown"
    user_id: str
    content: str  # Cleaned text (mentions stripped)
    raw_content: str  # Original text before cleaning
    mentioned: bool  # Whether the bot was @mentioned
    timestamp: str  # ISO format create time


# ── Message parsing ──────────────────────────────────────────────


def _extract_command_text(text: str, mention_keys: list[str]) -> str:
    """Strip @bot mentions from message text.

    Uses both precise mention-key removal and a regex fallback for
    the ``@_user_N`` pattern that Feishu uses internally.
    """
    for key in mention_keys:
        if key:
            text = text.replace(key, "")
    # Regex fallback: remove @_user_<digits> patterns
    text = re.sub(r'@_user_\d+\s*', '', text)
    return ' '.join(text.split())


def _parse_event_to_message(
    event_dict: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Convert a raw Feishu Stream event dict into a unified message dict.

    Returns ``None`` for non-text messages or unparseable events.
    """
    try:
        event = event_dict.get("event") or {}
        header = event_dict.get("header") or {}
        message = event.get("message")
        sender = event.get("sender")

        if not message:
            return None

        message_type = message.get("message_type") or ""
        if message_type != "text":
            logger.debug("[Feishu Stream] Ignoring non-text message: %s", message_type)
            return None

        content_str = message.get("content") or "{}"
        try:
            content_json = json.loads(content_str)
            raw_content = content_json.get("text", "")
        except json.JSONDecodeError:
            raw_content = content_str

        mentions = message.get("mentions") or []
        mention_keys = [m.get("key", "") for m in mentions]
        content = _extract_command_text(raw_content, mention_keys)
        mentioned = "@" in raw_content or bool(mentions)

        user_id = ""
        if sender and sender.get("sender_id"):
            sid = sender["sender_id"]
            user_id = sid.get("open_id") or sid.get("user_id") or ""

        chat_type_str = message.get("chat_type") or ""
        if chat_type_str == "group":
            chat_type = "group"
        elif chat_type_str == "p2p":
            chat_type = "private"
        else:
            chat_type = "unknown"

        return {
            "message_id": message.get("message_id") or "",
            "chat_id": message.get("chat_id") or "",
            "chat_type": chat_type,
            "user_id": user_id,
            "content": content,
            "raw_content": raw_content,
            "mentioned": mentioned,
            "timestamp": message.get("create_time") or "",
        }
    except Exception:
        logger.exception("[Feishu Stream] Failed to parse event message")
        return None


# ── Card building ────────────────────────────────────────────────


def _build_reply_card(
    text: str,
    header: Optional[str] = None,
) -> dict[str, Any]:
    """Build a Feishu interactive card for reply messages.

    Args:
        text: Markdown/lark_md formatted content.
        header: Optional card header title.

    Returns:
        Feishu interactive card payload dict with ``msg_type`` and ``card``.
    """
    card: dict[str, Any] = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": text},
                }
            ],
        },
    }
    if header:
        card["card"]["header"] = {
            "title": {"tag": "plain_text", "content": header},
        }
    return card


def _chunk_content(
    content: str,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> list[str]:
    """Split content into byte-sized chunks, preserving UTF-8 boundaries.

    Args:
        content: The full text to split.
        max_bytes: Maximum bytes per chunk.

    Returns:
        List of content chunks, each ≤ max_bytes when UTF-8 encoded.
    """
    content_bytes = len(content.encode("utf-8"))
    if content_bytes <= max_bytes:
        return [content]

    chunks: list[str] = []
    current: list[str] = []
    current_size = 0

    for char in content:
        char_size = len(char.encode("utf-8"))
        if current_size + char_size > max_bytes and current:
            chunks.append("".join(current))
            current = []
            current_size = 0
        current.append(char)
        current_size += char_size

    if current:
        chunks.append("".join(current))

    # Add page markers when chunked
    total = len(chunks)
    if total > 1:
        return [
            chunk + _PAGE_MARKER_TEMPLATE.format(current=i + 1, total=total)
            for i, chunk in enumerate(chunks)
        ]
    return chunks


# ── Reply client ─────────────────────────────────────────────────


class FeishuReplyClient:
    """Send reply messages to Feishu via the Open API.

    Uses the ``lark-oapi`` SDK to call ``im.v1.message.reply`` (when
    replying to a specific message) or ``im.v1.message.create`` (when
    sending to a chat directly).
    """

    def __init__(self, app_id: str, app_secret: str):
        if not FEISHU_SDK_AVAILABLE:
            raise ImportError("lark-oapi SDK not installed. Run: pip install lark-oapi")

        self._app_id = app_id
        self._app_secret = app_secret
        self._client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
        self._max_bytes = _DEFAULT_MAX_BYTES

    # -- public API --

    def reply_text(
        self,
        message_id: str,
        text: str,
        header: Optional[str] = None,
    ) -> bool:
        """Reply to a specific message with text rendered as an interactive card.

        Long messages are automatically chunked.
        """
        chunks = _chunk_content(text, self._max_bytes)
        success_all = True
        for i, chunk in enumerate(chunks):
            chunk_header = header if i == 0 else f"{header} ({i + 1}/{len(chunks)})" if header else None
            if not self._send_card(chunk, message_id=message_id, header=chunk_header):
                success_all = False
        return success_all

    def send_to_chat(
        self,
        chat_id: str,
        text: str,
        receive_id_type: str = "chat_id",
        header: Optional[str] = None,
    ) -> bool:
        """Send a message to a chat (not a reply to a specific message)."""
        chunks = _chunk_content(text, self._max_bytes)
        success_all = True
        for i, chunk in enumerate(chunks):
            chunk_header = header if i == 0 else f"{header} ({i + 1}/{len(chunks)})" if header else None
            if not self._send_card(
                chunk,
                chat_id=chat_id,
                receive_id_type=receive_id_type,
                header=chunk_header,
            ):
                success_all = False
        return success_all

    # -- internal --

    def _send_card(
        self,
        text: str,
        message_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        receive_id_type: str = "chat_id",
        header: Optional[str] = None,
    ) -> bool:
        """Post an interactive card via the Feishu API."""
        card = _build_reply_card(text, header=header)
        content_json = json.dumps(card)

        try:
            if message_id:
                request = (
                    ReplyMessageRequest.builder()
                    .message_id(message_id)
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .content(content_json)
                        .msg_type("interactive")
                        .build()
                    )
                    .build()
                )
                response = self._client.im.v1.message.reply(request)
            else:
                request = (
                    CreateMessageRequest.builder()
                    .receive_id_type(receive_id_type)
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(chat_id)
                        .content(content_json)
                        .msg_type("interactive")
                        .build()
                    )
                    .build()
                )
                response = self._client.im.v1.message.create(request)

            if not response.success():
                logger.error(
                    "[Feishu Stream] Card send failed: code=%s msg=%s",
                    response.code,
                    response.msg,
                )
                return False

            logger.debug("[Feishu Stream] Card sent successfully")
            return True

        except Exception:
            logger.exception("[Feishu Stream] Card send exception")
            return False


# ── Message handler ──────────────────────────────────────────────


class FeishuStreamHandler:
    """Process incoming Feishu messages with per-conversation FIFO ordering.

    Messages from the same conversation are processed sequentially to
    preserve conversation context, while different conversations can
    run in parallel (via ThreadPoolExecutor, default 8 workers).

    Args:
        on_message: Async callback receiving a ``FeishuMessage`` dict and
            returning a reply text string (or None to skip reply).
        reply_client: The ``FeishuReplyClient`` used to send replies.
    """

    def __init__(
        self,
        on_message: Callable[[dict[str, Any]], Optional[str]],
        reply_client: FeishuReplyClient,
        max_workers: int = 8,
    ):
        self._on_message = on_message
        self._reply_client = reply_client
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="feishu-msg",
        )
        self._pending: dict[str, deque[dict[str, Any]]] = {}
        self._active: set[str] = set()
        self._lock = threading.Lock()
        self._shutdown = False

    # -- SDK callback (called from ws.Client thread) --

    def handle_message(self, event: "P2ImMessageReceiveV1") -> None:
        """Receive a ``P2ImMessageReceiveV1`` event from the SDK WebSocket.

        This is called from the SDK's callback thread. We parse
        quickly and hand off to the worker pool.
        """
        try:
            event_dict = _sdk_event_to_dict(event)
            msg = _parse_event_to_message(event_dict)
            if msg is None:
                return

            logger.info(
                "[Feishu Stream] Incoming: msg_id=%s chat_type=%s user=%s content=%s",
                msg["message_id"],
                msg["chat_type"],
                msg["user_id"],
                msg["content"][:200],
            )
            self._enqueue(msg)

        except Exception:
            logger.exception("[Feishu Stream] Error in handle_message")

    # -- internal: per-conversation FIFO queue --

    def _conversation_key(self, msg: dict[str, Any]) -> str:
        chat_id = msg.get("chat_id", "unknown")
        user_id = msg.get("user_id", "unknown")
        return f"{chat_id}:{user_id}"

    def _enqueue(self, msg: dict[str, Any]) -> None:
        if self._shutdown:
            return
        key = self._conversation_key(msg)
        should_start = False
        with self._lock:
            self._pending.setdefault(key, deque()).append(msg)
            if key not in self._active:
                self._active.add(key)
                should_start = True
        if should_start:
            try:
                self._executor.submit(self._drain, key)
            except RuntimeError:
                with self._lock:
                    self._active.discard(key)
                    self._pending.pop(key, None)

    def _drain(self, key: str) -> None:
        while True:
            with self._lock:
                queue = self._pending.get(key)
                if not queue:
                    self._pending.pop(key, None)
                    self._active.discard(key)
                    return
                msg = queue.popleft()
            self._process(msg)

    def _process(self, msg: dict[str, Any]) -> None:
        try:
            reply_text = self._on_message(msg)
            if reply_text and msg.get("message_id"):
                self._reply_client.reply_text(
                    message_id=msg["message_id"],
                    text=reply_text,
                    header="花木兰工作台",
                )
        except Exception:
            logger.exception("[Feishu Stream] Message processing failed")

    # -- lifecycle --

    def shutdown(self, wait: bool = False) -> None:
        """Stop accepting new messages and drain pending work."""
        self._shutdown = True
        with self._lock:
            self._pending.clear()
            self._active.clear()
        self._executor.shutdown(wait=wait)


def _sdk_event_to_dict(event: "P2ImMessageReceiveV1") -> dict[str, Any]:
    """Convert a lark-oapi SDK event object to a plain dict.

    The SDK event has nested objects with snake_case attribute names.
    We flatten to a dict for consistent parsing.
    """
    result: dict[str, Any] = {"header": {}, "event": {}}

    if hasattr(event, "header") and event.header:
        result["header"] = {
            "event_id": getattr(event.header, "event_id", ""),
            "event_type": getattr(event.header, "event_type", ""),
            "create_time": getattr(event.header, "create_time", ""),
            "token": getattr(event.header, "token", ""),
            "app_id": getattr(event.header, "app_id", ""),
        }

    if hasattr(event, "event") and event.event:
        ev = event.event
        message = getattr(ev, "message", None)
        sender = getattr(ev, "sender", None)

        event_data: dict[str, Any] = {}
        if message:
            mentions_list = []
            raw_mentions = getattr(message, "mentions", None)
            if raw_mentions:
                for m in raw_mentions:
                    mentions_list.append({
                        "key": getattr(m, "key", ""),
                        "name": getattr(m, "name", ""),
                    })

            event_data["message"] = {
                "message_id": getattr(message, "message_id", ""),
                "chat_id": getattr(message, "chat_id", ""),
                "chat_type": getattr(message, "chat_type", ""),
                "message_type": getattr(message, "message_type", ""),
                "content": getattr(message, "content", ""),
                "mentions": mentions_list,
                "create_time": getattr(message, "create_time", ""),
            }

        if sender:
            sid = getattr(sender, "sender_id", None)
            event_data["sender"] = {
                "sender_id": {
                    "open_id": getattr(sid, "open_id", "") if sid else "",
                    "user_id": getattr(sid, "user_id", "") if sid else "",
                }
            }

        result["event"] = event_data

    return result


# ── Stream client ────────────────────────────────────────────────


class FeishuStreamClient:
    """Feishu Stream Mode WebSocket client.

    Wraps the ``lark-oapi`` WebSocket client to provide a simple
    start/stop interface. Runs the WebSocket in a background daemon
    thread so it can coexist with the FastAPI server.

    Usage::

        client = FeishuStreamClient(on_message=my_callback)
        client.start_background()
        # ... server runs ...
        client.stop()
    """

    def __init__(
        self,
        on_message: Callable[[dict[str, Any]], Optional[str]],
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
    ):
        if not FEISHU_SDK_AVAILABLE:
            raise ImportError("lark-oapi SDK not installed. Run: pip install lark-oapi")

        self._app_id = app_id
        self._app_secret = app_secret
        self._on_message = on_message

        self._ws_client: Any = None
        self._reply_client: Optional[FeishuReplyClient] = None
        self._handler: Optional[FeishuStreamHandler] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._stop_event = threading.Event()

    # -- properties --

    @property
    def enabled(self) -> bool:
        """True when app credentials are configured."""
        return bool(self._app_id and self._app_secret)

    @property
    def running(self) -> bool:
        return self._running

    # -- public API --

    def start_background(self) -> None:
        """Start the WebSocket connection in a background daemon thread."""
        if not self.enabled:
            logger.warning("[Feishu Stream] Not configured, skipping start")
            return
        if self._thread and self._thread.is_alive():
            logger.warning("[Feishu Stream] Already running")
            return

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="FeishuStreamClient",
        )
        self._thread.start()
        logger.info("[Feishu Stream] Background client started")

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the WebSocket to stop and wait for the thread to join."""
        self._running = False
        self._stop_event.set()
        if self._handler:
            self._handler.shutdown(wait=True)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("[Feishu Stream] Thread did not stop within timeout")
        logger.info("[Feishu Stream] Client stopped")

    # -- internal --

    def _setup(self) -> None:
        """Create the reply client, handler, and event dispatcher."""
        self._reply_client = FeishuReplyClient(self._app_id, self._app_secret)
        self._handler = FeishuStreamHandler(
            on_message=self._on_message,
            reply_client=self._reply_client,
        )

    def _build_event_handler(self) -> Any:
        """Build the SDK EventDispatcherHandler."""
        self._setup()
        return (
            lark.EventDispatcherHandler.builder(
                encrypt_key="",
                verification_token="",
                level=lark.LogLevel.WARNING,
            )
            .register_p2_im_message_receive_v1(self._handler.handle_message)
            .build()
        )

    def _run_loop(self) -> None:
        """Main loop: connect, handle messages, reconnect on failure."""
        import time

        while self._running and not self._stop_event.is_set():
            try:
                event_handler = self._build_event_handler()
                self._ws_client = lark_ws.Client(
                    app_id=self._app_id,
                    app_secret=self._app_secret,
                    event_handler=event_handler,
                    log_level=lark.LogLevel.WARNING,
                    auto_reconnect=True,
                )
                logger.info("[Feishu Stream] WebSocket connecting...")
                self._ws_client.start()
            except Exception:
                logger.exception("[Feishu Stream] WebSocket error")
                if self._running and not self._stop_event.is_set():
                    logger.info("[Feishu Stream] Reconnecting in 5s...")
                    time.sleep(5)
```


- [ ] **Step 2: Run tests to verify basic functions pass**

Run: `cd backend && python -m pytest tests/test_feishu_stream.py -v`

Expected: Tests for `_extract_command_text`, `_build_reply_card`, `_chunk_content`, and `_parse_event_to_message` should PASS. Tests for `FeishuReplyClient` that mock the SDK may also pass depending on mock setup. SDK-dependent tests will be adjusted in the next step.

- [ ] **Step 3: Commit**

```bash
git add backend/src/personal_assistant/api/feishu_stream.py
git commit -m "feat: implement Feishu Stream Mode module (client, handler, reply)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: Integrate Stream client into FastAPI lifespan

**Files:**
- Modify: `backend/src/personal_assistant/api/server.py` (add lifespan integration)

This task requires reading `server.py` to find the exact lifespan/lifespan context manager and the `run_user_turn` agent interface. The changes are:

- [ ] **Step 1: Read current lifespan block in server.py**

Find the `@asynccontextmanager` lifespan function or the app startup/shutdown events.

- [ ] **Step 2: Add import for Feishu stream client**

Add near the existing feishu import (line 70):

```python
from personal_assistant.api.feishu_stream import FeishuStreamClient
```

- [ ] **Step 3: Create the Stream client factory function**

Add after the imports block, a function that creates the on_message callback wrapping `AgentHarness.run_user_turn()`:

```python
def _build_feishu_message_handler(
    harness: AgentHarness,
    memory: PostgresMemory,
) -> "Callable[[dict[str, Any]], Optional[str]]":
    """Build the on_message callback for FeishuStreamClient.

    Routes incoming Feishu messages to the agent harness and returns
    the agent's text response for reply.
    """
    import asyncio

    def handle_message(msg: dict[str, Any]) -> Optional[str]:
        content = msg.get("content", "").strip()
        if not content:
            return None

        chat_id = msg.get("chat_id", "feishu")
        user_id = msg.get("user_id", "unknown")
        thread_id = f"feishu:{chat_id}:{user_id}"

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                response = loop.run_until_complete(
                    harness.run_user_turn(
                        thread_id=thread_id,
                        message=content,
                        memory=memory,
                    )
                )
            finally:
                loop.close()

            if response and response.message:
                return response.message
            return None
        except Exception:
            logger.exception("[Feishu Stream] Agent processing failed")
            return "抱歉，处理您的消息时出错了，请稍后重试。"

    return handle_message
```

- [ ] **Step 4: Start/stop the Stream client in the lifespan**

In the lifespan function, after the existing startup code and before yield, add:

```python
# Start Feishu Stream client (bidirectional bot)
settings = get_settings()
_feishu_stream_client: Optional[FeishuStreamClient] = None

if settings.feishu_stream_enabled and settings.feishu_app_id:
    _feishu_stream_client = FeishuStreamClient(
        on_message=_build_feishu_message_handler(harness, memory),
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
    )
    _feishu_stream_client.start_background()
    logger.info("Feishu Stream client started (bidirectional mode)")
```

And in the shutdown section (after yield), add:

```python
# Stop Feishu Stream client
if _feishu_stream_client and _feishu_stream_client.running:
    _feishu_stream_client.stop(timeout=5.0)
    logger.info("Feishu Stream client stopped")
```

- [ ] **Step 5: Verify server starts without errors**

Run: `cd backend && python -c "from personal_assistant.api.server import app; print('OK')"`

Expected: No errors (may show warnings if SDK not installed, which is fine for import check).

- [ ] **Step 6: Run existing tests to check for regressions**

Run: `cd backend && python -m pytest tests/test_feishu_notifier.py tests/test_otel_webhook.py -v`

Expected: All existing Feishu-related tests should still PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/personal_assistant/api/server.py
git commit -m "feat: integrate Feishu Stream client into FastAPI lifespan

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Add integration tests

**Files:**
- Modify: `backend/tests/test_feishu_stream.py` (append integration test class)

- [ ] **Step 1: Add FeishuStreamHandler integration tests**

Append to `backend/tests/test_feishu_stream.py`:

```python
# ── FeishuStreamHandler integration tests ─────────────────────────


class TestFeishuStreamHandler:
    """Tests for the message handler FIFO processing."""

    def test_processes_message_and_replies(self):
        """Handler should call on_message and reply via client."""
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

    def test_empty_content_not_replied(self):
        """Handler should skip reply when on_message returns None."""
        reply_client = MagicMock()

        def on_message(msg):
            return None  # No reply

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
```

- [ ] **Step 2: Run all Feishu Stream tests**

Run: `cd backend && python -m pytest tests/test_feishu_stream.py -v`

Expected: ALL tests PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_feishu_stream.py
git commit -m "test: add integration tests for FeishuStreamHandler and client

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: Final verification — full test suite

- [ ] **Step 1: Run the complete test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=60`

Expected: All existing tests continue to pass. New Feishu Stream tests also pass.

- [ ] **Step 2: Quick server startup smoke test**

Run:
```bash
cd backend && timeout 5 python -c "
from personal_assistant.config import get_settings
from personal_assistant.api.feishu_stream import FeishuStreamClient, FEISHU_SDK_AVAILABLE
s = get_settings()
print(f'SDK available: {FEISHU_SDK_AVAILABLE}')
print(f'Stream enabled: {s.feishu_stream_enabled}')
print(f'App ID: {\"configured\" if s.feishu_app_id else \"not set\"}')
print('All imports OK')
" || echo "Timeout OK (imports succeeded before timeout)"
```

Expected: No import errors.

- [ ] **Step 3: Commit (if any final adjustments needed)**

```bash
git commit -m "chore: final verification — all tests pass, imports clean"
```

---

## Self-Review

### 1. Spec coverage

| Requirement | Task(s) |
|---|---|
| Receive Feishu messages (no public URL) | Task 4 (`FeishuStreamClient` + WebSocket) |
| Parse Feishu events → unified format | Task 4 (`_parse_event_to_message`, `_extract_command_text`) |
| Route messages to LangGraph agent | Task 5 (`_build_feishu_message_handler` → `harness.run_user_turn`) |
| Reply with agent responses | Task 4 (`FeishuReplyClient.reply_text`) |
| Interactive card format for replies | Task 4 (`_build_reply_card`, `_send_card`) |
| Configuration via env vars | Task 2 (config.py) |
| Preserve existing webhook push | No changes to `FeishuNotifier` — unchanged |
| TDD (test first) | Tasks 3→4 (test written before implementation) |
| Graceful degradation (no SDK / no config) | Task 4 (`FEISHU_SDK_AVAILABLE` check, `enabled` property) |

### 2. Placeholder scan

No TBDs, TODOs, or "implement later" patterns found. All code is complete. Error handling is explicit in every function. Edge cases covered: empty text, non-text messages, missing fields, SDK not installed, credentials not configured.

### 3. Type consistency

- `_parse_event_to_message` returns `Optional[dict[str, Any]]` — used consistently in `FeishuStreamHandler.handle_message`
- `on_message` callback signature: `Callable[[dict[str, Any]], Optional[str]]` — consistent across `FeishuStreamHandler.__init__`, `FeishuStreamClient.__init__`, and `_build_feishu_message_handler`
- `FeishuReplyClient.reply_text` takes `(message_id, text, header)` — consistent with `FeishuStreamHandler._process`
