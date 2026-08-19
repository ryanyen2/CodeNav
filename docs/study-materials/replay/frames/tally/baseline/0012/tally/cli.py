"""The command.

    tally summarise fixtures/statement.csv      write the summary beside it
    tally summarise fixtures/statement.csv -    print it instead
    tally summarise fixtures/statement.csv --by-week    group by week, not month
    tally check fixtures/                       summarise everything, write nothing
    tally check fixtures/ --by-week             the same, by week

`check` exists so a change can be run over every statement in one go. It writes
nothing, which is what makes it safe to run against a folder you care about.

The merchant rules and the other settings are in tally/rules.toml. They are read
once, here, and passed down; nothing below reads a file. A mistake in that file
stops the run with a message naming it.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .settings import RulesError, load
from .summary import summarise

# A weekly summary is written beside the monthly one rather than over it. The two
# answer different questions and somebody who asked for both should end up with
# both, not with whichever they ran last.
SUFFIX = {"month": ".md", "week": ".weekly.md"}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(__doc__)
        return 0

    by = "week" if "--by-week" in args else "month"
    args = [arg for arg in args if arg != "--by-week"]
    if not args:
        print("--by-week needs a command", file=sys.stderr)
        return 2

    try:
        settings = load()
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
        result = summarise(path.read_text(encoding="utf-8"), settings, by=by)
        if len(rest) > 1 and rest[1] == "-":
            sys.stdout.write(result.text())
        else:
            path.with_suffix(SUFFIX[by]).write_text(result.text(), encoding="utf-8")
        print(f"{path.name}: {result.line()}")
        return 0

    if command == "check":
        root = Path(rest[0] if rest else "fixtures")
        found = sorted(root.glob("*.csv"))
        if not found:
            print(f"nothing to check in {root}", file=sys.stderr)
            return 2
        for path in found:
            summary = summarise(path.read_text(encoding="utf-8"), settings, by=by)
            print(f"{path.name}: {summary.line()}")
        print(f"checked {len(found)} statements")
        return 0

    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
