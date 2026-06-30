from pathlib import Path

from personal_assistant.memory.long_term import LongTermMemoryStore


def test_long_term_memory_creates_core_files(tmp_path: Path) -> None:
    store = LongTermMemoryStore(tmp_path)

    store.ensure_files()

    assert (tmp_path / "USER.md").read_text(encoding="utf-8").startswith("# User")
    assert (tmp_path / "SYSTEM.md").read_text(encoding="utf-8").startswith("# System")
    assert (tmp_path / "MEMORY.md").read_text(encoding="utf-8").startswith("# Memory Index")


def test_long_term_memory_appends_one_index_line_per_link(tmp_path: Path) -> None:
    store = LongTermMemoryStore(tmp_path)

    link = store.add_memory(
        slug="user-preference-tabs",
        title="user-preference-tabs",
        summary="User prefers tabs",
        body="User prefers tabs over spaces.",
    )

    assert link == tmp_path / "user-preference-tabs.md"
    assert link.read_text(encoding="utf-8") == "User prefers tabs over spaces.\n"
    index_lines = (tmp_path / "MEMORY.md").read_text(encoding="utf-8").splitlines()
    assert "- [user-preference-tabs](user-preference-tabs.md) - User prefers tabs" in index_lines


def test_long_term_memory_replaces_existing_index_line_for_same_slug(tmp_path: Path) -> None:
    store = LongTermMemoryStore(tmp_path)

    store.add_memory(
        slug="system-boundary",
        title="system-boundary",
        summary="Old summary",
        body="Old body.",
    )
    store.add_memory(
        slug="system-boundary",
        title="system-boundary",
        summary="New summary",
        body="New body.",
    )

    index_lines = [
        line
        for line in (tmp_path / "MEMORY.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("- [system-boundary]")
    ]
    assert index_lines == ["- [system-boundary](system-boundary.md) - New summary"]
    assert (tmp_path / "system-boundary.md").read_text(encoding="utf-8") == "New body.\n"


def test_read_all_returns_all_memory_content(tmp_path: Path) -> None:
    store = LongTermMemoryStore(tmp_path)
    store.ensure_files()

    # Write meaningful USER.md and SYSTEM.md
    (tmp_path / "USER.md").write_text("# User\n\nUser prefers to be called Yazuki.\n", encoding="utf-8")
    (tmp_path / "SYSTEM.md").write_text("# System\n\nThis project uses Python.\n", encoding="utf-8")

    # Add a memory entry
    store.add_memory(
        slug="user-preferred-tabs",
        title="Tab preference",
        summary="User prefers tabs",
        body="User prefers tabs over spaces for indentation.",
    )

    result = store.read_all()

    assert "User prefers to be called Yazuki" in result
    assert "This project uses Python" in result
    assert "Tab preference" in result
    assert "User prefers tabs over spaces" in result
    assert "Memory Index" in result


def test_read_all_handles_empty_store(tmp_path: Path) -> None:
    store = LongTermMemoryStore(tmp_path)
    store.ensure_files()

    result = store.read_all()

    # Should return empty string or minimal content when files are just templates
    assert isinstance(result, str)


class FakeCache:
    def __init__(self):
        self.values = {}
        self.set_calls = []

    async def get_json(self, key):
        return self.values.get(key)

    async def set_json(self, key, value, ttl_seconds):
        self.values[key] = value
        self.set_calls.append((key, ttl_seconds))

    async def delete(self, key):
        self.values.pop(key, None)

    async def delete_pattern(self, pattern):
        return None

    async def close(self):
        return None


async def test_read_all_cached_reuses_value_when_memory_files_are_unchanged(tmp_path: Path) -> None:
    store = LongTermMemoryStore(tmp_path)
    store.ensure_files()
    (tmp_path / "USER.md").write_text("# User\n\nFirst value.\n", encoding="utf-8")
    cache = FakeCache()

    first = await store.read_all_cached(cache, ttl_seconds=60)
    second = await store.read_all_cached(cache, ttl_seconds=60)

    assert "First value" in first
    assert second == first
    assert len(cache.set_calls) == 1


async def test_read_all_cached_misses_when_memory_file_size_changes(tmp_path: Path) -> None:
    store = LongTermMemoryStore(tmp_path)
    store.ensure_files()
    (tmp_path / "USER.md").write_text("# User\n\nShort.\n", encoding="utf-8")
    cache = FakeCache()

    first = await store.read_all_cached(cache, ttl_seconds=60)
    (tmp_path / "USER.md").write_text("# User\n\nA much longer memory value.\n", encoding="utf-8")
    second = await store.read_all_cached(cache, ttl_seconds=60)

    assert "Short" in first
    assert "much longer" in second
    assert len(cache.set_calls) == 2
