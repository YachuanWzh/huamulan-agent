# -*- coding: utf-8 -*-
"""
Feishu (飞书) Stream Mode bidirectional communication.

Uses the lark-oapi SDK's WebSocket long connection to receive messages
from Feishu users (no public URL required) and route them to the
LangGraph agent, then reply with agent responses.

Architecture::

    Feishu Server <-> ws.Client (WebSocket) -> EventDispatcherHandler
        -> FeishuStreamHandler._enqueue (FIFO per conversation)
        -> on_message callback -> AgentHarness.run_user_turn()
        -> FeishuReplyClient.reply_text() -> im.v1.message.reply

Reference: daily_stock_analysis/bot/platforms/feishu_stream.py
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── Lazy SDK import ──────────────────────────────────────────────
try:
    import lark_oapi as lark  # noqa: F401
    from lark_oapi import ws as lark_ws  # noqa: F401
    from lark_oapi.api.im.v1 import (  # noqa: F401
        CreateMessageRequest,
        CreateMessageRequestBody,
        P2ImMessageReceiveV1,
        ReplyMessageRequest,
        ReplyMessageRequestBody,
    )

    FEISHU_SDK_AVAILABLE = True
except ImportError:
    FEISHU_SDK_AVAILABLE = False
    lark = None  # type: ignore[assignment]
    lark_ws = None  # type: ignore[assignment]
    logger.warning(
        "[Feishu Stream] lark-oapi SDK not installed. "
        "Stream Mode unavailable. Run: pip install lark-oapi"
    )

# ── Constants ────────────────────────────────────────────────────

_DEFAULT_MAX_BYTES = 20000
_PAGE_MARKER_TEMPLATE = "\n\n---\n*(第 {current}/{total} 页)*"


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

    Page markers (e.g. "*(第 1/3 页)*") are accounted for in the byte
    budget so the final chunk + marker still fits within ``max_bytes``.

    Args:
        content: The full text to split.
        max_bytes: Maximum bytes per chunk (including page marker).

    Returns:
        List of content chunks, each ≤ max_bytes when UTF-8 encoded.
    """
    content_bytes = len(content.encode("utf-8"))
    if content_bytes <= max_bytes:
        return [content]

    # Estimate how many chunks we'll need, then compute marker overhead.
    # We use a conservative estimate: assume max_bytes per chunk.
    estimated_total = (content_bytes + max_bytes - 1) // max_bytes
    # Pre-compute the worst-case marker for budget calculation
    worst_marker = _PAGE_MARKER_TEMPLATE.format(
        current=estimated_total, total=estimated_total
    )
    marker_overhead = len(worst_marker.encode("utf-8"))
    effective_max = max(max_bytes - marker_overhead, 1)

    chunks: list[str] = []
    current: list[str] = []
    current_size = 0

    for char in content:
        char_size = len(char.encode("utf-8"))
        if current_size + char_size > effective_max and current:
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
    return [content]


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
            chunk_header = (
                header
                if (i == 0 or not header)
                else f"{header} ({i + 1}/{len(chunks)})"
            )
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
            chunk_header = (
                header
                if (i == 0 or not header)
                else f"{header} ({i + 1}/{len(chunks)})"
            )
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
        on_message: Callback receiving a parsed message dict and returning
            a reply text string (or ``None`` to skip reply).
        reply_client: The ``FeishuReplyClient`` used to send replies.
        max_workers: Thread pool size for parallel conversation processing.
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
