from typing import Any, Protocol


class AsyncCache(Protocol):
    async def get_json(self, key: str) -> Any | None:
        ...

    async def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        ...

    async def delete(self, key: str) -> None:
        ...

    async def delete_pattern(self, pattern: str) -> None:
        ...

    async def close(self) -> None:
        ...


class NoopCache:
    async def get_json(self, key: str) -> Any | None:
        return None

    async def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None

    async def delete_pattern(self, pattern: str) -> None:
        return None

    async def close(self) -> None:
        return None
