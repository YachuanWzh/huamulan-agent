import hashlib
import hmac

import pytest

from personal_assistant.governance.alert_guard import (
    AlertIngressGuard,
    AlertReplayError,
    AlertSignatureError,
    InMemoryAlertIngressStore,
)


def _signature(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_valid_signature_is_accepted_once():
    guard = AlertIngressGuard("secret", InMemoryAlertIngressStore(), clock=lambda: 1000)
    body, timestamp = b'{"alerts":[]}', "1000"
    await guard.verify(timestamp, _signature("secret", timestamp, body), body)


@pytest.mark.asyncio
async def test_replay_and_stale_signature_are_rejected():
    guard = AlertIngressGuard("secret", InMemoryAlertIngressStore(), clock=lambda: 1000)
    body, timestamp = b'{}', "1000"
    signature = _signature("secret", timestamp, body)
    await guard.verify(timestamp, signature, body)
    with pytest.raises(AlertReplayError):
        await guard.verify(timestamp, signature, body)
    with pytest.raises(AlertSignatureError):
        await guard.verify("600", _signature("secret", "600", body), body)
