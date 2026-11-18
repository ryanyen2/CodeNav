from codoc.index.lang.base import LanguageAdapter
from codoc.index.lang.python import PythonAdapter
from codoc.index.lang.typescript import TypeScriptAdapter


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


EXTENSIONS = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
}


def detect_language(file_path: str) -> str | None:
    """Detect language from file extension. Returns None if unsupported."""
    from pathlib import Path
    return EXTENSIONS.get(Path(file_path).suffix.lower())
