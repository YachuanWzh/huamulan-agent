from pathlib import Path

from personal_assistant.memory.long_term import LongTermMemoryStore
from personal_assistant.tools.basic import build_basic_tools


def _tool_map(workspace: Path, **kwargs):
    return {tool.name: tool for tool in build_basic_tools(workspace, **kwargs)}


class TestBasicToolRegistration:
    def test_build_basic_tools_exposes_shell_and_file_tools(self, tmp_path: Path):
        tools = _tool_map(tmp_path)

        assert set(tools) == {
            "shell_command",
            "read_file",
            "write_file",
            "list_directory",
            "search_files",
            "save_conversation_memory",
        }


class TestShellCommandTool:
    def test_shell_command_runs_inside_workspace(self, tmp_path: Path):
        tools = _tool_map(tmp_path)

        result = tools["shell_command"].invoke(
            {"command": "echo hello", "cwd": ".", "timeout_seconds": 5}
        )

        assert "exit_code=0" in result
        assert "hello" in result

    def test_shell_command_preserves_utf8_subprocess_output_on_windows_locale(
        self, tmp_path: Path
    ):
        tools = _tool_map(tmp_path)

        result = tools["shell_command"].invoke(
            {
                "command": (
                    "python -c \"import sys; "
                    "sys.stdout.buffer.write(b'\\xe2\\x94\\x94 test-result')\""
                ),
                "cwd": ".",
                "timeout_seconds": 5,
            }
        )

        assert "exit_code=0" in result
        assert "└ test-result" in result

    def test_shell_command_rejects_cwd_outside_workspace(self, tmp_path: Path):
        tools = _tool_map(tmp_path)

        result = tools["shell_command"].invoke(
            {"command": "echo unsafe", "cwd": "..", "timeout_seconds": 5}
        )

        assert "SecurityError" in result
        assert "outside workspace" in result


class TestFileTools:
    def test_write_then_read_file_inside_workspace(self, tmp_path: Path):
        tools = _tool_map(tmp_path)

        write_result = tools["write_file"].invoke(
            {"path": "notes/today.txt", "content": "milk\ncalendar", "append": False}
        )
        read_result = tools["read_file"].invoke({"path": "notes/today.txt"})

        assert "wrote 13 bytes" in write_result
        assert read_result == "milk\ncalendar"

    def test_file_tools_reject_paths_outside_workspace(self, tmp_path: Path):
        tools = _tool_map(tmp_path)

        result = tools["read_file"].invoke({"path": "../secret.txt"})

        assert "SecurityError" in result
        assert "outside workspace" in result

    def test_list_directory_returns_relative_children(self, tmp_path: Path):
        (tmp_path / "notes").mkdir()
        (tmp_path / "notes" / "today.txt").write_text("hello", encoding="utf-8")
        tools = _tool_map(tmp_path)

        result = tools["list_directory"].invoke({"path": "."})

        assert "notes/" in result

    def test_search_files_finds_matching_content(self, tmp_path: Path):
        (tmp_path / "notes").mkdir()
        (tmp_path / "notes" / "today.txt").write_text("buy milk", encoding="utf-8")
        (tmp_path / "notes" / "later.txt").write_text("call dentist", encoding="utf-8")
        tools = _tool_map(tmp_path)

        result = tools["search_files"].invoke(
            {"query": "milk", "path": ".", "glob": "*.txt", "max_results": 10}
        )

        assert "notes/today.txt" in result
        assert "notes/later.txt" not in result


class TestLongTermMemoryTool:
    async def test_save_conversation_memory_writes_markdown_memory(self, tmp_path: Path):
        store = LongTermMemoryStore(tmp_path / ".memory")
        tools = _tool_map(tmp_path, long_term_memory=store)

        result = await tools["save_conversation_memory"].ainvoke(
            {
                "slug": "user-prefers-tabs",
                "title": "user-prefers-tabs",
                "summary": "User prefers tabs",
                "body": "User prefers tabs over spaces.",
            }
        )

        assert "saved long-term memory" in result
        assert (tmp_path / ".memory" / "user-prefers-tabs.md").exists()
        assert "User prefers tabs" in (tmp_path / ".memory" / "MEMORY.md").read_text(
            encoding="utf-8"
        )
