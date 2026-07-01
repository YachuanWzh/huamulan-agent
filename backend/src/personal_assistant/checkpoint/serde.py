import zlib
from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


COMPRESSED_MARKER_PREFIX = "zlib+"


class CompressedJsonPlusSerializer:
    """LangGraph JsonPlus serializer with zlib compression for larger byte payloads."""

    def __init__(self, *, compress_threshold_bytes: int = 1024):
        self.inner = JsonPlusSerializer()
        self.compress_threshold_bytes = compress_threshold_bytes

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        marker, data = self.inner.dumps_typed(obj)
        if len(data) < self.compress_threshold_bytes:
            return marker, data
        return f"{COMPRESSED_MARKER_PREFIX}{marker}", zlib.compress(data)

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        marker, payload = data
        if marker.startswith(COMPRESSED_MARKER_PREFIX):
            marker = marker[len(COMPRESSED_MARKER_PREFIX) :]
            payload = zlib.decompress(payload)
        return self.inner.loads_typed((marker, payload))
