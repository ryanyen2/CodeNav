"""Encoding triage for source files.

Priority:
1. utf-8-sig (handles BOM, strips it automatically)
2. utf-8
3. charset-normalizer best-match (if installed)
4. latin-1 last-resort (never fails, may mangle non-Latin)

BOM is stripped before returning so downstream fingerprinting is stable.
Returns (text, encoding_used) on success, raises UnicodeDecodeError on total failure.
"""

from __future__ import annotations

from pathlib import Path


def read_text(path: str | Path, *, max_bytes: int = 1 * 1024 * 1024) -> tuple[str, str]:
    """Read *path* and return (text, encoding_used).

    Files larger than *max_bytes* raise ValueError so callers can skip them.
    """
    raw = Path(path).read_bytes()
    if len(raw) > max_bytes:
        raise ValueError(f"File too large to index: {path} ({len(raw)} bytes > {max_bytes})")
    return decode_bytes(raw)


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """Decode *raw* bytes, stripping BOM, returning (text, encoding)."""
    for enc in ("utf-8-sig", "utf-8"):
        try:
            text = raw.decode(enc)
            # Strip BOM if utf-8-sig didn't already (shouldn't happen but be safe).
            text = text.lstrip("\ufeff")
            return text, enc
        except (UnicodeDecodeError, LookupError):
            pass

    try:
        from charset_normalizer import from_bytes

        result = from_bytes(raw).best()
        if result is not None:
            text = str(result).lstrip("\ufeff")
            return text, result.encoding or "unknown"
    except ImportError:
        pass

    # latin-1 never raises — use as absolute fallback.
    text = raw.decode("latin-1").lstrip("\ufeff")
    return text, "latin-1"


def is_likely_binary(raw: bytes, *, sample_size: int = 2048) -> bool:
    """Return True if the leading bytes suggest a binary file."""
    sample = raw[:sample_size]
    if b"\x00" in sample:
        return True
    # Heuristic: >30% non-text bytes → binary.
    non_text = sum(1 for b in sample if b < 9 or (14 <= b <= 31))
    return non_text > len(sample) * 0.30 if sample else False
