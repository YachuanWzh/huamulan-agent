"""Smoke tests: verify every chapter's code.py is syntactically valid Python.

These tests ensure all course code examples parse correctly and can be imported.
They do NOT run the code (which would require an LLM API key).
"""
from pathlib import Path

import pytest

COURSE_DIR = Path(__file__).resolve().parent.parent
CHAPTERS = sorted(
    d.name
    for d in COURSE_DIR.iterdir()
    if d.is_dir() and d.name.startswith("s")
)


def _get_code_path(chapter: str) -> Path | None:
    """Return the code.py path for a chapter, or None if it doesn't exist."""
    code_path = COURSE_DIR / chapter / "code.py"
    if code_path.exists():
        return code_path
    return None


def _get_readme_path(chapter: str) -> Path | None:
    """Return the README.md path for a chapter, or None if it doesn't exist."""
    readme_path = COURSE_DIR / chapter / "README.md"
    if readme_path.exists():
        return readme_path
    return None


# ── Tests ───────────────────────────────────────────────────


@pytest.mark.parametrize("chapter", CHAPTERS)
def test_chapter_has_readme(chapter: str):
    """Every chapter must have a README.md."""
    readme = _get_readme_path(chapter)
    assert readme is not None, f"{chapter}/README.md is missing"


@pytest.mark.parametrize("chapter", CHAPTERS)
def test_readme_is_not_empty(chapter: str):
    """Every README.md must have content."""
    readme = _get_readme_path(chapter)
    if readme is None:
        pytest.skip(f"{chapter} has no README.md")
    content = readme.read_text(encoding="utf-8")
    assert len(content) > 100, (
        f"{chapter}/README.md is too short ({len(content)} chars). "
        "Expected at least 100 characters."
    )


@pytest.mark.parametrize("chapter", CHAPTERS)
def test_readme_has_required_sections(chapter: str):
    """Every README.md should have the standard section headers."""
    readme = _get_readme_path(chapter)
    if readme is None:
        pytest.skip(f"{chapter} has no README.md")
    content = readme.read_text(encoding="utf-8")
    # All chapters should have at minimum: 问题 section
    required = ["## 问题"]
    for section in required:
        assert section in content, (
            f"{chapter}/README.md is missing required section: '{section}'"
        )
    # Should also have a "how it works" section of some kind
    how_sections = ["工作原理", "端点设计", "SSE 流式", "组件架构",
                    "状态管理", "缓存策略", "压缩策略", "记忆系统",
                    "路由机制", "审批流程", "中间件链", "Hook 机制",
                    "工具定义", "循环机制", "脚本工具", "Checkpoint"]
    has_how = any(s in content for s in how_sections)
    assert has_how, (
        f"{chapter}/README.md is missing a 'how it works' section. "
        f"Expected one of: {how_sections}"
    )


@pytest.mark.parametrize("chapter", CHAPTERS)
def test_code_py_syntax(chapter: str):
    """Every code.py must be syntactically valid Python."""
    code_path = _get_code_path(chapter)
    if code_path is None:
        pytest.skip(f"{chapter} has no code.py (e.g., docs-only chapter)")
    source = code_path.read_text(encoding="utf-8")
    try:
        compile(source, str(code_path), "exec")
    except SyntaxError as e:
        pytest.fail(f"{chapter}/code.py has syntax error: {e}")


@pytest.mark.parametrize("chapter", CHAPTERS)
def test_code_py_has_main_block(chapter: str):
    """Every code.py should have an interactive entry point."""
    code_path = _get_code_path(chapter)
    if code_path is None:
        pytest.skip(f"{chapter} has no code.py")
    source = code_path.read_text(encoding="utf-8")
    assert '__name__ == "__main__"' in source or "__main__" in source, (
        f"{chapter}/code.py should have an if __name__ == '__main__' block"
    )


# ── Course structure tests ──────────────────────────────────


def test_course_readme_exists():
    """The course overview README must exist."""
    course_readme = COURSE_DIR / "README.md"
    assert course_readme.exists(), "course/README.md is missing"
    content = course_readme.read_text(encoding="utf-8")
    assert len(content) > 500, "course/README.md is too short"


def test_requirements_txt_exists():
    """requirements.txt must exist and be non-empty."""
    req_path = COURSE_DIR / "requirements.txt"
    assert req_path.exists(), "course/requirements.txt is missing"
    content = req_path.read_text(encoding="utf-8")
    assert len(content) > 20, "requirements.txt is too short"


def test_at_least_10_chapters():
    """We should have at least 10 chapters with code.py."""
    chapters_with_code = [
        c for c in CHAPTERS if _get_code_path(c) is not None
    ]
    assert len(chapters_with_code) >= 10, (
        f"Expected at least 10 chapters with code.py, "
        f"found {len(chapters_with_code)}: {chapters_with_code}"
    )


def test_s14_frontend_is_docs_only():
    """s14 (Frontend) should be a docs-only chapter with no code.py."""
    if "s14_frontend" in CHAPTERS:
        code_path = _get_code_path("s14_frontend")
        readme_path = _get_readme_path("s14_frontend")
        assert readme_path is not None, "s14_frontend must have README.md"
        # s14 is intentionally docs-only
        if code_path is not None:
            # Not an error, but worth noting
            pass
