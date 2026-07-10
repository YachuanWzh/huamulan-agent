"""Versioned policy storage and activation behavior."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from .models import GovernancePolicy, PolicyVersion


class PolicyStore(Protocol):
    """Persistence boundary for immutable policy versions."""

    def append(self, policy: PolicyVersion) -> PolicyVersion: ...

    def get(self, version: int) -> PolicyVersion | None: ...

    def active(self) -> PolicyVersion | None: ...


class InMemoryPolicyStore:
    """Small in-memory store used by tests and local bootstrap code."""

    def __init__(self) -> None:
        self._versions: list[PolicyVersion] = []

    def append(self, policy: PolicyVersion) -> PolicyVersion:
        self._versions = [
            item.model_copy(update={"is_active": False}) if item.is_active else item
            for item in self._versions
        ]
        stored = policy.model_copy(deep=True)
        self._versions.append(stored)
        return stored.model_copy(deep=True)

    def get(self, version: int) -> PolicyVersion | None:
        item = next((item for item in self._versions if item.version == version), None)
        return item.model_copy(deep=True) if item is not None else None

    def active(self) -> PolicyVersion | None:
        item = next((item for item in reversed(self._versions) if item.is_active), None)
        return item.model_copy(deep=True) if item is not None else None

    def next_version(self) -> int:
        return len(self._versions) + 1


class PolicyService:
    """Creates immutable policy snapshots and restores versions by copy."""

    def __init__(self, store: PolicyStore) -> None:
        self._store = store

    def create(self, document: GovernancePolicy) -> PolicyVersion:
        return self._store.append(
            PolicyVersion(
                version=self._next_version(),
                document=document.model_copy(deep=True),
                is_active=True,
                created_at=datetime.now(timezone.utc),
            )
        )

    def active(self) -> PolicyVersion:
        active = self._store.active()
        if active is None:
            raise LookupError("no active governance policy")
        return active

    def activate(self, version: int) -> PolicyVersion:
        historical = self._store.get(version)
        if historical is None:
            raise LookupError(f"governance policy version {version} does not exist")
        return self.create(historical.document)

    def _next_version(self) -> int:
        next_version = getattr(self._store, "next_version", None)
        if callable(next_version):
            return next_version()
        active = self._store.active()
        return 1 if active is None else active.version + 1
