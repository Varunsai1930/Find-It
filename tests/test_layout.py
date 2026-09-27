"""The package's dependency direction, checked from the source.

    core / store / ingest / summary   facts and rules
      <- research                     backtests over them
      <- pipeline                     the monthly run's steps
      <- cli / web                    entry points: parse arguments, render

A lower layer importing a higher one is how a rule ends up defined twice, so
this fails the build instead of leaving it to review.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "findit"

LAYER = {"core": 0, "store": 0, "ingest": 0, "summary": 0,
         "research": 1, "pipeline": 2, "cli": 3, "web": 3}


def _layer(module: str) -> int:
    """Layer of a dotted findit module name."""
    return LAYER[module.split(".")[1]]


def _findit_imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names if a.name.startswith("findit."))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module == "findit":
                names.update(f"findit.{a.name}" for a in node.names)
            elif node.module.startswith("findit."):
                names.add(node.module)
    return names


def test_no_module_imports_a_higher_layer():
    violations = []
    for path in sorted(PACKAGE.rglob("*.py")):
        parts = path.relative_to(ROOT).with_suffix("").parts
        if parts == ("findit", "__init__"):
            continue
        module = ".".join(parts)
        for imported in sorted(_findit_imports(path)):
            if _layer(imported) > _layer(module):
                violations.append(f"{module} imports {imported}")
    assert not violations, "\n".join(violations)


def test_all_code_lives_in_the_package():
    """Stray top-level modules are importable only by accident of the cwd."""
    assert sorted(p.name for p in ROOT.glob("*.py")) == []
