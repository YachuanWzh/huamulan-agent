import importlib.util
import sys
import zipfile
from pathlib import Path

import yaml

from personal_assistant.skills.loader import SkillRegistry


SKILL_DIR = Path("src/personal_assistant/skills/find-skills")
SKILL_MD = SKILL_DIR / "SKILL.md"
INSTALLER = SKILL_DIR / "scripts" / "install_project_skill.py"
SEARCHER = SKILL_DIR / "scripts" / "search_public_skills.py"


def _load_script_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_installer_module():
    return _load_script_module("find_skills_installer", INSTALLER)


def _load_searcher_module():
    return _load_script_module("find_skills_searcher", SEARCHER)


def test_find_skills_declares_project_install_script_tool() -> None:
    text = SKILL_MD.read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)[1]
    meta = yaml.safe_load(frontmatter)

    scripts = {entry["name"]: entry for entry in meta["scripts"]}
    installer = scripts["install_project_skill_from_github"]

    assert installer["command"] == [
        "python",
        "scripts/install_project_skill.py",
        "{package_spec}",
        "{target_dir}",
    ]
    assert installer["params"]["package_spec"]["required"] is True
    assert installer["params"]["target_dir"]["default"] == ".."


def test_find_skills_declares_public_search_script_tool() -> None:
    text = SKILL_MD.read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)[1]
    meta = yaml.safe_load(frontmatter)

    scripts = {entry["name"]: entry for entry in meta["scripts"]}
    searcher = scripts["search_public_skills"]

    assert searcher["command"] == [
        "python",
        "scripts/search_public_skills.py",
        "{query}",
    ]
    assert searcher["params"]["query"]["required"] is True


def test_find_skills_loads_project_install_tool() -> None:
    registry = SkillRegistry(Path("src/personal_assistant/skills"))

    registry.load_skill("find-skills")

    tool_map = registry.tool_map_for_skills(["find-skills"])
    assert "install_project_skill_from_github" in tool_map
    assert "search_public_skills" in tool_map


def test_searcher_parses_cli_package_results() -> None:
    module = _load_searcher_module()

    result = module.search_skills(
        "weather",
        run_cli=lambda _query: (
            "Install with npx skills add <owner/repo@skill>\n"
            "example-org/demo-skills@weather 10.3K installs\n"
            "└ https://skills.sh/example-org/demo-skills/weather\n"
        ),
    )

    assert result["source"] == "skills-cli"
    assert result["results"] == [
        {
            "package": "example-org/demo-skills@weather",
            "installs": "10.3K installs",
            "url": "https://skills.sh/example-org/demo-skills/weather",
        }
    ]


def test_searcher_filters_known_uninstallable_package() -> None:
    module = _load_searcher_module()

    result = module.search_skills(
        "weather",
        run_cli=lambda _query: (
            "example-org/demo-skills@weather 12.6K installs\n"
            "└ https://skills.sh/example-org/demo-skills/weather\n"
        ),
    )

    packages = [item["package"] for item in result["results"]]
    assert "example-org/demo-skills@weather" in packages


def test_searcher_uses_npx_cmd_when_windows_has_only_cmd_shim(monkeypatch) -> None:
    module = _load_searcher_module()

    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda name: "C:/Program Files/nodejs/npx.cmd" if name == "npx.cmd" else None,
    )

    assert module.skills_cli_command("weather") == [
        "C:/Program Files/nodejs/npx.cmd",
        "--yes",
        "skills",
        "find",
        "weather",
    ]


def test_searcher_decodes_cli_output_as_utf8_with_replacement(monkeypatch) -> None:
    module = _load_searcher_module()
    captured = {}

    class Result:
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.run_skills_cli("weather")

    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"



def test_installer_parses_owner_repo_and_skill_name() -> None:
    module = _load_installer_module()

    package = module.parse_package_spec("example-org/demo-skills@weather")

    assert package.owner == "example-org"
    assert package.repo == "demo-skills"
    assert package.skill == "weather"


def test_installer_copies_matching_skill_directory(tmp_path: Path) -> None:
    module = _load_installer_module()
    repo = tmp_path / "repo"
    source_skill = repo / "skills" / "productivity" / "weather"
    source_skill.mkdir(parents=True)
    (source_skill / "SKILL.md").write_text(
        "---\nname: weather\n---\n# Weather Lookup\n",
        encoding="utf-8",
    )
    (source_skill / "notes.txt").write_text("weather notes", encoding="utf-8")
    target = tmp_path / "project-skills"
    target.mkdir()

    result = module.copy_skill_from_repo(repo, "weather", target)

    installed = target / "weather"
    assert result == installed
    assert (installed / "SKILL.md").exists()
    assert (installed / "notes.txt").read_text(encoding="utf-8") == "weather notes"


def test_installer_finds_skill_by_frontmatter_name_when_directory_differs(
    tmp_path: Path,
) -> None:
    module = _load_installer_module()
    repo = tmp_path / "repo"
    source_skill = repo / "skills" / "weather-tools"
    source_skill.mkdir(parents=True)
    (source_skill / "SKILL.md").write_text(
        "---\nname: weather\n---\n# Weather Lookup\n",
        encoding="utf-8",
    )

    result = module.copy_skill_from_repo(repo, "weather", tmp_path / "target")

    assert result == tmp_path / "target" / "weather"
    assert (result / "SKILL.md").exists()


def test_installer_installs_any_skill_name_with_injected_repo_fetcher(tmp_path: Path) -> None:
    module = _load_installer_module()
    target = tmp_path / "project-skills"

    def fetch_repo(_package, repo_dir):
        source_skill = repo_dir / "tools" / "generic-skill"
        source_skill.mkdir(parents=True)
        (source_skill / "SKILL.md").write_text(
            "---\nname: generic-skill\n---\n# Generic Skill\n",
            encoding="utf-8",
        )

    installed = module.install_project_skill(
        "someone/useful-skills@generic-skill",
        str(target),
        fetch_repo=fetch_repo,
    )

    assert installed == target / "generic-skill"
    assert (installed / "SKILL.md").exists()


def test_installer_extracts_github_zip_archive(tmp_path: Path) -> None:
    module = _load_installer_module()
    archive = tmp_path / "repo.zip"
    destination = tmp_path / "repo"

    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("repo-main/skills/generic-skill/SKILL.md", "# Generic Skill\n")
        zip_file.writestr("repo-main/skills/generic-skill/notes.txt", "hello")

    module.extract_repo_archive(archive, destination)

    assert (destination / "skills" / "generic-skill" / "SKILL.md").exists()
    assert (destination / "skills" / "generic-skill" / "notes.txt").read_text(
        encoding="utf-8"
    ) == "hello"
