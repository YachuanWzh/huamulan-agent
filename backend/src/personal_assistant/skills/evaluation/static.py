import ast
import re

from personal_assistant.skills.base import Skill
from personal_assistant.skills.evaluation.models import StaticSkillMetrics


def evaluate_static_skill(skill: Skill) -> StaticSkillMetrics:
    python_files = _python_files(skill)
    return StaticSkillMetrics(
        skill_name=skill.name,
        description_tokens=_estimate_tokens(skill.description),
        skill_md_lines=_count_lines(skill.instructions_path),
        python_lines=sum(_count_lines(path) for path in python_files),
        max_cyclomatic_complexity=max(
            (_max_complexity_for_file(path) for path in python_files),
            default=0,
        ),
        tool_count=len(skill.tools) if skill.loaded else len(skill.script_decls),
    )


def _python_files(skill: Skill):
    files = []
    skill_py = skill.path / "skill.py"
    if skill_py.exists():
        files.append(skill_py)
    scripts_dir = skill.path / "scripts"
    if scripts_dir.exists():
        files.extend(sorted(scripts_dir.glob("*.py")))
    return files


def _estimate_tokens(text: str) -> int:
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
    words = re.findall(r"[A-Za-z0-9_]+", text)
    return len(cjk_chars) + len(words)


def _count_lines(path) -> int:
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeDecodeError):
        return 0


def _max_complexity_for_file(path) -> int:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return 0
    function_complexities = [
        _complexity(node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    return max(function_complexities, default=0)


def _complexity(node: ast.AST) -> int:
    complexity = 1
    branch_nodes = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.ExceptHandler,
        ast.IfExp,
        ast.BoolOp,
        ast.Match,
    )
    for child in ast.walk(node):
        if child is node:
            continue
        if isinstance(child, branch_nodes):
            complexity += 1
    return complexity
