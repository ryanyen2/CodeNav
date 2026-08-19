"""Finding and reading `scribe.toml`.

The file is optional. Without one every document converts exactly as it did
before, which is what keeps the library usable on its own.

    [defaults]
    repeat_share = 0.4

    [document."handbook.txt"]
    repeat_share = 0.8

A `[document."name"]` section applies to the document whose file name matches it.
Anything not named there falls back to `[defaults]`, and anything not in
`[defaults]` falls back to the built-in value.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from .settings import DEFAULTS, Settings

CONFIG_NAME = "scribe.toml"

KNOWN = {"repeat_share", "edge", "keep_hyphen"}


class ConfigError(ValueError):
    """The config file is there but says something scribe cannot act on."""


def find(start: Path) -> Path | None:
    """The nearest scribe.toml at or above `start`."""
    here = start if start.is_dir() else start.parent
    for folder in [here.resolve(), *here.resolve().parents]:
        candidate = folder / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def _checked(raw: dict, where: str) -> dict:
    unknown = sorted(set(raw) - KNOWN)
    if unknown:
        raise ConfigError(f"{where}: scribe has no setting called {', '.join(unknown)}")
    share = raw.get("repeat_share")
    if share is not None and not 0 < float(share) <= 1:
        raise ConfigError(f"{where}: repeat_share has to be above 0 and at most 1")
    edge = raw.get("edge")
    if edge is not None and int(edge) < 1:
        raise ConfigError(f"{where}: edge has to be at least 1")
    return raw


class Config:
    """A parsed scribe.toml: the defaults, and any per document overrides."""

    def __init__(self, defaults: Settings, per_document: dict[str, dict]):
        self.defaults = defaults
        self.per_document = per_document

    def for_document(self, name: str) -> Settings:
        """The settings for one document, its own section merged over the defaults."""
        return self.defaults.merged(**self.per_document.get(name, {}))


def load(path: Path | None) -> Config:
    """Read a config file, or hand back the built-in settings if there is none."""
    if path is None:
        return Config(DEFAULTS, {})
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    defaults = DEFAULTS.merged(**_checked(raw.get("defaults", {}), "[defaults]"))
    per_document = {
        name: _checked(section, f'[document."{name}"]')
        for name, section in (raw.get("document") or {}).items()
    }
    return Config(defaults, per_document)
