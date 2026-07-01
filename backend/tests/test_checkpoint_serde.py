from personal_assistant.checkpoint.serde import CompressedJsonPlusSerializer


def test_compressed_serializer_round_trips_payload() -> None:
    serde = CompressedJsonPlusSerializer(compress_threshold_bytes=1024)
    payload = {
        "id": "checkpoint-1",
        "channel_values": {
            "messages": [{"type": "human", "content": "hello"}],
            "selected_skills": ["resolve-time"],
        },
    }

    typed = serde.dumps_typed(payload)
    decoded = serde.loads_typed(typed)

    assert decoded == payload


def test_compressed_serializer_marks_large_payloads() -> None:
    serde = CompressedJsonPlusSerializer(compress_threshold_bytes=32)
    payload = {"content": "x" * 2048}

    marker, data = serde.dumps_typed(payload)

    assert marker.startswith("zlib+")
    assert len(data) < len(payload["content"])
    assert serde.loads_typed((marker, data)) == payload
