"""Layering rules, enforced: ui → services → core / db, and core stays pure.

Assumption: ``db`` may import ``core`` (domain dataclasses, enums and the column
order check) so repositories return domain objects. ``core`` imports nothing
from the project's other layers, nor Streamlit or SQLAlchemy.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN: dict[str, set[str]] = {
    "core": {"db", "services", "ui", "config", "streamlit", "sqlalchemy"},
    "db": {"services", "ui", "streamlit"},
    "services": {"ui", "streamlit"},
    "config": {"core", "db", "services", "ui"},
}


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_imports(layer: str) -> None:
    violations = []
    for path in sorted((ROOT / layer).rglob("*.py")):
        bad = imported_roots(path) & FORBIDDEN[layer]
        if bad:
            violations.append(f"{path.relative_to(ROOT)} imports {sorted(bad)}")
    assert not violations, "\n".join(violations)


def test_pages_do_not_touch_the_database() -> None:
    for path in sorted((ROOT / "ui").rglob("*.py")):
        assert not imported_roots(path) & {"db", "sqlalchemy"}, path


def test_no_print_or_bare_except() -> None:
    for layer in ("core", "db", "services", "ui", "config"):
        for path in (ROOT / layer).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                assert not (isinstance(node, ast.ExceptHandler) and node.type is None), path
                assert not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "print"
                ), path
