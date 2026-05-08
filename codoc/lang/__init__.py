from codoc.lang.base import Chunk, SymbolRef, LanguageAdapter
from codoc.lang.python import PythonAdapter
from codoc.lang.typescript import TypeScriptAdapter


def get_adapter(language: str) -> LanguageAdapter:
    """Return the appropriate adapter for the given language name."""
    adapters = {
        "python": PythonAdapter,
        "typescript": TypeScriptAdapter,
        "tsx": TypeScriptAdapter,
    }
    cls = adapters.get(language.lower())
    if cls is None:
        raise ValueError(
            f"No adapter for language: {language!r}. Supported: {list(adapters)}"
        )
    return cls()


def detect_language(file_path: str) -> str | None:
    """Detect language from file extension. Returns None if unsupported."""
    ext_map = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".mts": "typescript",
        ".cts": "typescript",
    }
    from pathlib import Path
    suffix = Path(file_path).suffix.lower()
    return ext_map.get(suffix)
