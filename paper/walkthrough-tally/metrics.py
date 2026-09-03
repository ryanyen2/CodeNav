"""Exact Inter / JetBrains Mono advance widths, so SVG text can be laid out by hand.

The figures place every run of text at an absolute x, which is the only way to put a
diff ground or a blame underline exactly under the words it is about. That needs real
glyph advances, not an average-width guess: a 3% error over a 90-character line is a
visible overhang. Inter ships as a variable font, so each weight is instanced once and
its horizontal metrics are read straight out of the instance.
"""
from __future__ import annotations

import base64
import functools
import io
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
from fontTools import subset

FONT_DIR = Path("/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav/vscode-codoc/assets/fonts")
INTER = FONT_DIR / "Inter.woff2"
MONO = FONT_DIR / "JetBrainsMono.woff2"
MONO_BOLD = FONT_DIR / "JetBrainsMono-Bold.woff2"


@functools.lru_cache(maxsize=8)
def _instance(path: str, wght: float | None):
    font = TTFont(path)
    if wght is not None and "fvar" in font:
        font = instancer.instantiateVariableFont(font, {"wght": wght, "opsz": 14}, inplace=False)
    return font


@functools.lru_cache(maxsize=8)
def _table(path: str, wght: float | None):
    font = _instance(path, wght)
    upem = font["head"].unitsPerEm
    cmap = font.getBestCmap()
    hmtx = font["hmtx"]
    return {cp: hmtx[name][0] / upem for cp, name in cmap.items()}, upem


def advance(ch: str, family: str = "doc", weight: int = 400) -> float:
    """Advance width of one character in em units."""
    path, w = (str(INTER), float(weight)) if family == "doc" else (
        (str(MONO_BOLD), None) if weight >= 600 else (str(MONO), None))
    table, _ = _table(path, w)
    cp = ord(ch)
    if cp in table:
        return table[cp]
    # Anything the face has no glyph for falls back to the space advance, which is
    # what the browser's own fallback will roughly cost us too.
    return table.get(ord("n"), 0.55)


def width(text: str, size: float, family: str = "doc", weight: int = 400,
          tracking: float = 0.0) -> float:
    """Rendered width in px. `tracking` is letter-spacing in em (Inter body is 0)."""
    if not text:
        return 0.0
    return sum(advance(c, family, weight) for c in text) * size + tracking * size * len(text)


@functools.lru_cache(maxsize=8)
def embed(family: str, weight: int, glyphs: str) -> str:
    """A base64 woff2 of exactly the glyphs a figure uses, for a self-contained SVG."""
    path, w = (str(INTER), float(weight)) if family == "doc" else (
        (str(MONO_BOLD), None) if weight >= 600 else (str(MONO), None))
    font = _instance(path, w)
    opts = subset.Options()
    opts.flavor = "woff2"
    opts.layout_features = ["*"]
    opts.desubroutinize = True
    sub = subset.Subsetter(options=opts)
    sub.populate(text=glyphs)
    # instantiateVariableFont hands back a live TTFont; subset it in place on a copy.
    buf = io.BytesIO()
    font.save(buf)
    buf.seek(0)
    copy = TTFont(buf)
    sub.subset(copy)
    out = io.BytesIO()
    copy.flavor = "woff2"
    copy.save(out)
    return base64.b64encode(out.getvalue()).decode("ascii")
