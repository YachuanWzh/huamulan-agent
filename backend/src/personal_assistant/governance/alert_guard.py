"""HMAC, replay, and rate-limit protection for alert ingress."""

from __future__ import annotations

import hashlib
import hmac
import time


class AlertIngressError(Exception):
    status_code = 400


class AlertSignatureError(AlertIngressError):
    status_code = 401


class AlertReplayError(AlertIngressError):
    status_code = 409


class AlertRateLimitError(AlertIngressError):
    status_code = 429


class InMemoryAlertIngressStore:
    def __init__(self) -> None:
        self._replays: dict[str, float] = {}
        self._requests: list[float] = []

    async def claim_replay(self, signature: str, now: float, ttl_seconds: int) -> bool:
        self._replays = {key: expiry for key, expiry in self._replays.items() if expiry > now}
        if signature in self._replays:
            return False
        self._replays[signature] = now + ttl_seconds
        return True

    async def allow(self, now: float, limit: int) -> bool:
        self._requests = [item for item in self._requests if item > now - 60]
        if len(self._requests) >= limit:
            return False
        self._requests.append(now)
        return True


class AlertIngressGuard:
    def __init__(self, secret: str, store: InMemoryAlertIngressStore, *, clock=time.time, freshness_seconds: int = 300, rate_limit_per_minute: int = 60) -> None:
        self._secret = secret
        self._store = store
        self._clock = clock
        self._freshness_seconds = freshness_seconds
        self._rate_limit_per_minute = rate_limit_per_minute

    async def verify(self, timestamp: str | None, signature: str | None, raw_body: bytes) -> None:
        if not self._secret or not timestamp or not signature:
            raise AlertSignatureError("signature headers are required")
        try:
            timestamp_value = int(timestamp)
        except ValueError as exc:
            raise AlertSignatureError("invalid timestamp") from exc
        now = self._clock()
        if abs(now - timestamp_value) > self._freshness_seconds:
            raise AlertSignatureError("timestamp expired")
        expected = hmac.new(self._secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise AlertSignatureError("invalid signature")
        if not await self._store.claim_replay(signature, now, self._freshness_seconds):
            raise AlertReplayError("replayed signature")
        if not await self._store.allow(now, self._rate_limit_per_minute):
            raise AlertRateLimitError("rate limit exceeded")
