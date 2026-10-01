"""findit <command> [options]: every command in one place.

    findit pipeline --load ... --prev 2026-07 --curr 2026-08
    findit <command> --help

Without installing, ``python3 -m findit.cli <command>`` does the same, and
each command also runs on its own as ``python3 -m findit.cli.<command>``.
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

_CLI_DIR = Path(__file__).resolve().parent
# In the order a month's work runs, then the occasional tools.
COMMANDS = ("download", "intake", "parse", "pipeline", "report", "shareholding", "prices", "backtest",
            "backtest_quarterly", "revalidate", "rebuild", "digest", "alias", "scheme_titles",
            "fixtures", "web", "coverage", "release", "refresh")


def _summary(name: str) -> str:
    """First paragraph of a command's docstring, read without importing it."""
    tree = ast.parse((_CLI_DIR / f"{name}.py").read_text(encoding="utf-8"))
    return " ".join((ast.get_docstring(tree) or "").split("\n\n")[0].split())


def usage() -> str:
    width = max(map(len, COMMANDS)) + 2
    return "\n".join([__doc__.splitlines()[0], "", "commands:"]
                     + [f"  {name.replace('_', '-'):<{width}}{_summary(name)}"
                        for name in COMMANDS])


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(usage())
        return 0 if argv else 2
    name = argv[0].replace("-", "_")
    if name not in COMMANDS:
        print(f"findit: unknown command {argv[0]!r}\n\n{usage()}", file=sys.stderr)
        return 2
    module = importlib.import_module(f"findit.cli.{name}")
    program, sys.argv[0] = sys.argv[0], f"findit {argv[0]}"  # names it in argparse's usage
    try:
        return int(module.main(argv[1:]) or 0)
    finally:
        sys.argv[0] = program


if __name__ == "__main__":
    raise SystemExit(main())
