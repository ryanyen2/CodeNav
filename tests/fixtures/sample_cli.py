"""Minimal Python fixture used by lang adapter tests."""

from __future__ import annotations

import argparse
from enum import Enum


class ArgEnum(Enum):
    """Sample enum used for CLI argument parsing."""

    OPTION_A = "a"
    OPTION_B = "b"

    @staticmethod
    def from_string(s: str) -> "ArgEnum":
        try:
            return cls(s)
        except ValueError:
            raise argparse.ArgumentTypeError(f"Unknown value: {s!r}")


def create_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser."""
    parser = argparse.ArgumentParser(description="Sample CLI")
    parser.add_argument("--option", type=ArgEnum.from_string, default=ArgEnum.OPTION_A)
    return parser


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()
    print(f"Running with option={args.option}")
