"""Writing scripts: one table of which characters belong to which class.

A constant written in characters means something different in a script that
needs two characters to say a word instead of six, and a string's script is
the first evidence of its language (``codoc.doclang``). Whether a script puts
spaces between words also decides how ``ingest`` cuts text into tokens: an
unspaced script is diffed per character.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScriptClass:
    name: str
    # Mean characters per word, INCLUDING the separator for spaced scripts. This
    # is the number that ports a character budget across scripts.
    chars_per_word: float


# Latin, Cyrillic, Greek, Arabic, Hebrew, Devanagari, and any other script that
# delimits words with whitespace and averages a Latin-ish word length.
SPACED = ScriptClass("spaced", chars_per_word=6.0)
# Han + kana: word boundaries are unmarked and a "word" is one or two characters.
LOGOGRAPHIC = ScriptClass("logographic", chars_per_word=2.0)
# Thai, Khmer, Lao, Myanmar: unspaced like Han, but alphabetic, so a word is
# several characters.
UNSPACED_ALPHA = ScriptClass("unspaced_alpha", chars_per_word=5.0)
# Hangul is spaced, but a syllable block packs what Latin spends 2 or 3
# characters on, so it needs its own word length.
HANGUL = ScriptClass("hangul", chars_per_word=3.5)

# The classes ``ingest`` diffs one character at a time. Hangul is spaced, but a
# syllable block is most of a word and a word carries its particles, so it is
# diffed per block too.
PER_CHARACTER = frozenset({LOGOGRAPHIC, UNSPACED_ALPHA, HANGUL})

# Codepoint ranges per non-default script class. Order matters only for reading;
# the ranges are disjoint. Anything unlisted is SPACED, the common case.
_RANGES: tuple[tuple[int, int, ScriptClass], ...] = (
    (0x3040, 0x30FF, LOGOGRAPHIC),    # hiragana + katakana
    (0x31F0, 0x31FF, LOGOGRAPHIC),    # katakana phonetic extensions
    (0x3400, 0x4DBF, LOGOGRAPHIC),    # CJK unified ext A
    (0x4E00, 0x9FFF, LOGOGRAPHIC),    # CJK unified
    (0xF900, 0xFAFF, LOGOGRAPHIC),    # CJK compatibility ideographs
    (0x20000, 0x3134F, LOGOGRAPHIC),  # CJK unified ext B…G
    (0x1100, 0x11FF, HANGUL),         # jamo
    (0x3130, 0x318F, HANGUL),         # compatibility jamo
    (0xA960, 0xA97F, HANGUL),         # jamo extended A
    (0xAC00, 0xD7AF, HANGUL),         # syllables
    (0x0E00, 0x0E7F, UNSPACED_ALPHA),  # thai
    (0x0E80, 0x0EFF, UNSPACED_ALPHA),  # lao
    (0x1000, 0x109F, UNSPACED_ALPHA),  # myanmar
    (0x1780, 0x17FF, UNSPACED_ALPHA),  # khmer
)


def script_of(ch: str) -> ScriptClass:
    """The script class of a single character (SPACED for anything unlisted)."""
    cp = ord(ch)
    for lo, hi, cls in _RANGES:
        if lo <= cp <= hi:
            return cls
    return SPACED
