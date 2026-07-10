from personal_assistant.governance.incidents import IncidentService, IncidentStatus


def test_create_incident_starts_open_with_alert_timeline_entry() -> None:
    service = IncidentService()

    incident = service.create_from_alert(
        alert_id="alert-1",
        severity="P0",
        title="Checkout is unavailable",
        service="checkout",
    )

    assert incident.status is IncidentStatus.OPEN
    assert incident.alert_id == "alert-1"
    assert incident.timeline[0].event_type == "alert_received"
    assert "Checkout is unavailable" in incident.timeline[0].message


def test_status_updates_append_timeline_entries_in_order() -> None:
    service = IncidentService()
    incident = service.create_from_alert("alert-1", "P0", "Outage", "checkout")

    updated = service.update(incident.id, status=IncidentStatus.INVESTIGATING, owner="Ada")

    assert updated.status is IncidentStatus.INVESTIGATING
    assert updated.owner == "Ada"
    assert [event.event_type for event in updated.timeline] == [
        "alert_received",
        "status_changed",
        "owner_changed",
    ]


def test_action_item_can_be_completed_without_mutating_original_snapshot() -> None:
    service = IncidentService()
    incident = service.create_from_alert("alert-1", "P1", "Latency", "search")
    with_action = service.add_action(incident.id, "Rollback deployment")

    completed = service.complete_action(incident.id, with_action.actions[0].id, completed=True)

    assert with_action.actions[0].completed is False
    assert completed.actions[0].completed is True
    assert completed.timeline[-1].event_type == "action_completed"
