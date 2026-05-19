"""Tests for fingerprint determinism and normalization."""

from __future__ import annotations

from codoc.core.fingerprint import (
    are_fingerprints_meaningfully_different,
    fingerprint_chunk,
    fingerprint_source,
)
from codoc.lang import get_adapter


def test_fingerprint_is_deterministic_across_runs() -> None:
    adapter = get_adapter("python")
    src = "def foo(x):\n    return x + 1\n"
    a = fingerprint_chunk(src, adapter)
    b = fingerprint_chunk(src, adapter)
    assert a == b
    # SHA-256 hex.
    assert len(a) == 64


def test_fingerprint_invariant_under_whitespace_changes() -> None:
    adapter = get_adapter("python")
    src1 = "def foo(x):\n    return x + 1\n"
    src2 = "def    foo(x):\n        return x  +  1\n\n\n"
    fp1 = fingerprint_chunk(src1, adapter)
    fp2 = fingerprint_chunk(src2, adapter)
    assert fp1 == fp2


def test_fingerprint_invariant_under_comment_changes() -> None:
    adapter = get_adapter("python")
    src1 = "def foo(x):\n    return x + 1\n"
    src2 = "# old comment\ndef foo(x):\n    # inner comment\n    return x + 1\n# trailing\n"
    fp1 = fingerprint_chunk(src1, adapter)
    fp2 = fingerprint_chunk(src2, adapter)
    assert fp1 == fp2


def test_fingerprint_changes_when_tokens_change() -> None:
    adapter = get_adapter("python")
    src1 = "def foo(x):\n    return x + 1\n"
    src2 = "def foo(x):\n    return x + 2\n"
    fp1 = fingerprint_chunk(src1, adapter)
    fp2 = fingerprint_chunk(src2, adapter)
    assert fp1 != fp2


def test_fingerprint_changes_when_identifier_renamed() -> None:
    adapter = get_adapter("python")
    src1 = "def foo(x):\n    return x + 1\n"
    src2 = "def bar(x):\n    return x + 1\n"
    fp1 = fingerprint_chunk(src1, adapter)
    fp2 = fingerprint_chunk(src2, adapter)
    assert fp1 != fp2


def test_fingerprint_source_alias_matches_chunk() -> None:
    adapter = get_adapter("python")
    src = "x = 1\n"
    assert fingerprint_chunk(src, adapter) == fingerprint_source(src, adapter)


def test_are_fingerprints_meaningfully_different() -> None:
    fp_a = "a" * 64
    fp_b = "b" * 64
    assert are_fingerprints_meaningfully_different(fp_a, fp_b) is True
    assert are_fingerprints_meaningfully_different(fp_a, fp_a) is False


def test_fingerprint_typescript_invariant_under_whitespace() -> None:
    adapter = get_adapter("typescript")
    src1 = "function foo(x: number) { return x + 1; }"
    src2 = "function   foo(x: number)\n{\n  return  x +  1;\n}\n"
    fp1 = fingerprint_chunk(src1, adapter)
    fp2 = fingerprint_chunk(src2, adapter)
    assert fp1 == fp2
