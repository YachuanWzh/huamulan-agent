"""Dual-write alert persistence: sync Redis + async PostgreSQL.

Redis is the fast hot path for real-time queries; PostgreSQL is the
durable cold path for history and audit. Every save writes to Redis
synchronously and to PostgreSQL synchronously (both are async IO, but
we await both — PG write is cheap enough that fire-and-forget is not
worth the risk of silent data loss on crash).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Redis key patterns
ALERT_KEY = "otel:alert:{alert_id}"
ALERT_LIST_KEY = "otel:alert:list"
ALERT_TTL_SECONDS = 86400  # 24 hours
ALERT_LIST_MAX_SIZE = 500


class AlertPersistence:
    """Sync write to Redis + PostgreSQL for OTEL alerts.

    Initialisation::

        persistence = AlertPersistence(
            redis_client=redis_client,
            postgres_memory=postgres_memory,
        )

    All methods are safe to call when either backend is ``None`` — they
    silently skip the unavailable backend.
    """

    def __init__(
        self,
        redis_client: Any | None = None,
        postgres_memory: Any | None = None,
    ):
        self.redis = redis_client
        self.pg = postgres_memory

    async def save_alert(self, alert_data: dict) -> None:
        """Persist a new alert to both Redis and PostgreSQL."""
        alert_id = alert_data["id"]

        # 1. Redis (hot path — fast reads)
        if self.redis is not None:
            try:
                alert_json = json.dumps(alert_data, ensure_ascii=False, default=str)
                key = ALERT_KEY.format(alert_id=alert_id)
                await self.redis.set(key, alert_json, ex=ALERT_TTL_SECONDS)
                # Maintain time-ordered sorted set for listing
                score = datetime.now(timezone.utc).timestamp()
                await self.redis.zadd(ALERT_LIST_KEY, {alert_id: score})
                # Trim list to max size
                await self.redis.zremrangebyrank(
                    ALERT_LIST_KEY, 0, -(ALERT_LIST_MAX_SIZE + 1)
                )
            except Exception:
                logger.exception("Redis alert save failed for %s", alert_id)

        # 2. PostgreSQL (cold path — durable)
        if self.pg is not None:
            try:
                await self.pg.upsert_otel_alert(alert_data)
            except Exception:
                logger.exception("PostgreSQL alert save failed for %s", alert_id)

    async def update_alert(self, alert_id: str, **fields: Any) -> dict | None:
        """Update specific fields of an alert in both backends.

        Returns the updated alert dict, or ``None`` if the alert was not
        found in either backend.
        """
        alert_data: dict | None = None

        # Update Redis first (fast path)
        if self.redis is not None:
            try:
                key = ALERT_KEY.format(alert_id=alert_id)
                raw = await self.redis.get(key)
                if raw:
                    alert_data = json.loads(raw)
                    alert_data.update(fields)
                    await self.redis.set(
                        key,
                        json.dumps(alert_data, ensure_ascii=False, default=str),
                        ex=ALERT_TTL_SECONDS,
                    )
            except Exception:
                logger.exception("Redis alert update failed for %s", alert_id)

        # Fallback: fetch from PostgreSQL if not in Redis
        if alert_data is None and self.pg is not None:
            try:
                alert_data = await self.pg.get_otel_alert(alert_id)
                if alert_data:
                    alert_data.update(fields)
            except Exception:
                logger.exception("PostgreSQL alert fetch for update failed for %s", alert_id)

        if alert_data is None:
            return None

        # Persist updated data to PostgreSQL
        if self.pg is not None:
            try:
                await self.pg.upsert_otel_alert(alert_data)
            except Exception:
                logger.exception("PostgreSQL alert update failed for %s", alert_id)

        return alert_data

    async def get_alert(self, alert_id: str) -> dict | None:
        """Get a single alert, Redis-first with PostgreSQL fallback."""
        # Try Redis first (hot path)
        if self.redis is not None:
            try:
                key = ALERT_KEY.format(alert_id=alert_id)
                raw = await self.redis.get(key)
                if raw:
                    return json.loads(raw)
            except Exception:
                logger.exception("Redis alert get failed for %s", alert_id)

        # Fallback to PostgreSQL
        if self.pg is not None:
            try:
                return await self.pg.get_otel_alert(alert_id)
            except Exception:
                logger.exception("PostgreSQL alert get failed for %s", alert_id)

        return None

    async def list_alerts(self, limit: int = 50) -> list[dict]:
        """List recent alerts, Redis-first with PostgreSQL fallback."""
        # Try Redis sorted set first (hot path)
        if self.redis is not None:
            try:
                members = await self.redis.zrevrange(ALERT_LIST_KEY, 0, limit - 1)
                if members:
                    keys = [
                        ALERT_KEY.format(
                            alert_id=mid.decode() if isinstance(mid, bytes) else str(mid)
                        )
                        for mid in members
                    ]
                    raw_list = await self.redis.mget(keys)
                    alerts: list[dict] = []
                    for raw in raw_list:
                        if raw:
                            try:
                                alerts.append(json.loads(raw))
                            except json.JSONDecodeError:
                                continue
                    if alerts:
                        return alerts
            except Exception:
                logger.exception("Redis alert list failed")

        # Fallback to PostgreSQL
        if self.pg is not None:
            try:
                return await self.pg.list_otel_alerts(limit)
            except Exception:
                logger.exception("PostgreSQL alert list failed")

        return []

    async def close(self) -> None:
        """Close backend connections."""
        if self.redis is not None:
            try:
                await self.redis.aclose()
            except Exception:
                pass
