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

import pytest

from findit.cli import __main__ as findit_cli

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
    """Only the explicit Vercel adapter lives outside the installed package.

    Vercel imports a root ASGI instance; application behavior stays in findit.
    Requiring this exact set still rejects every other stray top-level module.
    """
    assert sorted(p.name for p in ROOT.glob("*.py")) == ["app.py"]


def test_every_command_is_reachable_through_findit(capsys):
    """A new findit/cli module must be listed, and each one's --help must run."""

    modules = {p.stem for p in (PACKAGE / "cli").glob("*.py") if not p.stem.startswith("__")}
    assert set(findit_cli.COMMANDS) == modules
    for name in findit_cli.COMMANDS:
        with pytest.raises(SystemExit) as done:
            findit_cli.main([name.replace("_", "-"), "--help"])
        assert done.value.code == 0, name
    assert findit_cli.main(["no-such-command"]) == 2
    assert "unknown command" in capsys.readouterr().err
