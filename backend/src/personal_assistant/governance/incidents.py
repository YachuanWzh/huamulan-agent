"""In-memory incident command-center domain service."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    MITIGATED = "mitigated"
    CLOSED = "closed"


class IncidentTimelineEvent(BaseModel):
    """An immutable-looking event shown in the incident timeline."""

    model_config = ConfigDict(frozen=True)

    id: str
    event_type: str
    message: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncidentAction(BaseModel):
    """A concrete recovery task attached to an incident."""

    model_config = ConfigDict(frozen=True)

    id: str
    description: str
    completed: bool = False
    created_at: datetime
    completed_at: datetime | None = None


class Incident(BaseModel):
    """Current incident view, including its append-only timeline."""

    model_config = ConfigDict(frozen=True)

    id: str
    alert_id: str
    severity: str
    title: str
    service: str
    status: IncidentStatus
    owner: str | None = None
    rca_thread_id: str | None = None
    rca_result: str | None = None
    created_at: datetime
    updated_at: datetime
    timeline: list[IncidentTimelineEvent] = Field(default_factory=list)
    actions: list[IncidentAction] = Field(default_factory=list)


class IncidentService:
    """Owns incident state while keeping returned snapshots isolated from callers."""

    def __init__(self) -> None:
        self._incidents: dict[str, Incident] = {}

    def create_from_alert(
        self, alert_id: str, severity: str, title: str, service: str
    ) -> Incident:
        now = _now()
        incident = Incident(
            id=str(uuid4()),
            alert_id=alert_id,
            severity=severity,
            title=title,
            service=service,
            status=IncidentStatus.OPEN,
            created_at=now,
            updated_at=now,
            timeline=[
                _event(
                    "alert_received",
                    f"Alert received: {title}",
                    {"alert_id": alert_id, "severity": severity, "service": service},
                    now,
                )
            ],
        )
        self._incidents[incident.id] = incident
        return incident.model_copy(deep=True)

    def get(self, incident_id: str) -> Incident:
        return self._incident(incident_id).model_copy(deep=True)

    def list(self, status: IncidentStatus | None = None, limit: int = 100) -> list[Incident]:
        incidents = sorted(self._incidents.values(), key=lambda item: item.updated_at, reverse=True)
        if status is not None:
            incidents = [item for item in incidents if item.status is status]
        return [item.model_copy(deep=True) for item in incidents[:limit]]

    def update(
        self,
        incident_id: str,
        *,
        status: IncidentStatus | None = None,
        owner: str | None = None,
    ) -> Incident:
        incident = self._incident(incident_id)
        events: list[IncidentTimelineEvent] = []
        now = _now()
        changes: dict[str, Any] = {}
        if status is not None and status is not incident.status:
            changes["status"] = status
            events.append(_event("status_changed", f"Status changed to {status.value}", {}, now))
        if owner is not None and owner != incident.owner:
            changes["owner"] = owner
            events.append(_event("owner_changed", f"Owner changed to {owner}", {}, now))
        if not changes:
            return incident.model_copy(deep=True)
        updated = incident.model_copy(
            update={
                **changes,
                "updated_at": now,
                "timeline": [*incident.timeline, *events],
            },
            deep=True,
        )
        self._incidents[incident_id] = updated
        return updated.model_copy(deep=True)

    def add_action(self, incident_id: str, description: str) -> Incident:
        incident = self._incident(incident_id)
        now = _now()
        action = IncidentAction(id=str(uuid4()), description=description, created_at=now)
        updated = incident.model_copy(
            update={
                "actions": [*incident.actions, action],
                "timeline": [
                    *incident.timeline,
                    _event("action_created", f"Action created: {description}", {"action_id": action.id}, now),
                ],
                "updated_at": now,
            },
            deep=True,
        )
        self._incidents[incident_id] = updated
        return updated.model_copy(deep=True)

    def complete_action(self, incident_id: str, action_id: str, *, completed: bool) -> Incident:
        incident = self._incident(incident_id)
        action = next((item for item in incident.actions if item.id == action_id), None)
        if action is None:
            raise LookupError(f"incident action {action_id} does not exist")
        if action.completed is completed:
            return incident.model_copy(deep=True)
        now = _now()
        changed_action = action.model_copy(
            update={"completed": completed, "completed_at": now if completed else None}
        )
        updated = incident.model_copy(
            update={
                "actions": [changed_action if item.id == action_id else item for item in incident.actions],
                "timeline": [
                    *incident.timeline,
                    _event(
                        "action_completed" if completed else "action_reopened",
                        f"Action {'completed' if completed else 'reopened'}: {action.description}",
                        {"action_id": action_id},
                        now,
                    ),
                ],
                "updated_at": now,
            },
            deep=True,
        )
        self._incidents[incident_id] = updated
        return updated.model_copy(deep=True)

    def _incident(self, incident_id: str) -> Incident:
        incident = self._incidents.get(incident_id)
        if incident is None:
            raise LookupError(f"incident {incident_id} does not exist")
        return incident


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(event_type: str, message: str, metadata: dict[str, Any], now: datetime) -> IncidentTimelineEvent:
    return IncidentTimelineEvent(
        id=str(uuid4()), event_type=event_type, message=message, metadata=metadata, created_at=now
    )
