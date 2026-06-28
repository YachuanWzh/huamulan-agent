from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool


class PostgresMemory:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: AsyncConnectionPool | None = None
        self.checkpointer: AsyncPostgresSaver | None = None

    async def start(self) -> None:
        self.pool = AsyncConnectionPool(
            conninfo=self.database_url,
            open=False,
            kwargs={"autocommit": True},
        )
        await self.pool.open()
        self.checkpointer = AsyncPostgresSaver(self.pool)
        await self.checkpointer.setup()

    async def stop(self) -> None:
        if self.pool is not None:
            await self.pool.close()

    async def replay(self, thread_id: str) -> list[dict[str, Any]]:
        if self.checkpointer is None:
            raise RuntimeError("Postgres memory is not started")
        states: list[dict[str, Any]] = []
        config = {"configurable": {"thread_id": thread_id}}
        async for checkpoint in self.checkpointer.alist(config):
            states.append(_serialize_checkpoint(checkpoint))
        return states


def _serialize_checkpoint(checkpoint: Any) -> dict[str, Any]:
    payload = checkpoint.model_dump(mode="json") if hasattr(checkpoint, "model_dump") else checkpoint
    return {"checkpoint": payload}
