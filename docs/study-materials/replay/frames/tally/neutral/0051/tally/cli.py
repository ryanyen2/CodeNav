"""The command.

    tally summarise fixtures/statement.csv      write the summary beside it
    tally summarise fixtures/statement.csv -    print it instead
    tally check fixtures/                       summarise everything, write nothing

Either command takes:

    --by-week            group by week rather than by month
    --rules PATH         use a different rules file

`check` exists so a change can be run over every statement in one go. It writes
nothing, which is what makes it safe to run against a folder you care about.

`--by-week` is a different cut of the same statement, not an extra section: the
rows, the rules and the categories are identical and only the grouping changes.
Writing to a file, it goes to the same `.md` the monthly summary would, because
the alternative leaves a stale second file that nothing will ever update.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .rules import RulesError, default, load
from .summary import summarise


def _take_flag(args: list[str], name: str) -> bool:
    """Remove `--name` from args, saying whether it was there."""
    if name in args:
        args.remove(name)
        return True
    return False


def _take_value(args: list[str], name: str) -> str | None:
    """Remove `--name VALUE` or `--name=VALUE` from args, returning VALUE."""
    for position, arg in enumerate(args):
        if arg == name:
            if position + 1 >= len(args):
                raise ValueError(f"{name} needs a path")
            value = args[position + 1]
            del args[position:position + 2]
            return value
        if arg.startswith(f"{name}="):
            del args[position]
            return arg.split("=", 1)[1]
    return None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(__doc__)
        return 0

    try:
        by = "week" if _take_flag(args, "--by-week") else "month"
        rules_path = _take_value(args, "--rules")
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    unknown = [arg for arg in args[1:] if arg.startswith("--")]
    if unknown:
        # Rather than treating it as a filename and reporting that no such file
        # exists, which is true and unhelpful.
        print(f"unknown option: {unknown[0]}", file=sys.stderr)
        return 2

    try:
        rules = default() if rules_path is None else load(rules_path)
    except RulesError as exc:
        print(exc, file=sys.stderr)
        return 2

    command, rest = args[0], args[1:]
    if command == "summarise":
        if not rest:
            print("summarise needs a file", file=sys.stderr)
            return 2
        path = Path(rest[0])
        if not path.is_file():
            print(f"no such file: {path}", file=sys.stderr)
            return 2
        try:
            result = summarise(path.read_text(encoding="utf-8"), rules, by)
        except Unmatched as exc:
            print(f"{path.name}: {exc}", file=sys.stderr)
            return 2
        if len(rest) > 1 and rest[1] == "-":
            sys.stdout.write(result.text())
        else:
            path.with_suffix(".md").write_text(result.text(), encoding="utf-8")
        print(f"{path.name}: {result.line()}")
        return 0

    if command == "check":
        root = Path(rest[0] if rest else "fixtures")
        found = sorted(root.glob("*.csv"))
        if not found:
            print(f"nothing to check in {root}", file=sys.stderr)
            return 2
        for path in found:
            summary = summarise(path.read_text(encoding="utf-8"), rules, by)
            print(f"{path.name}: {summary.line()}")
        print(f"checked {len(found)} statements")
        return 0

    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
