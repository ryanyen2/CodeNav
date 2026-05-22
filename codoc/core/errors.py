"""Structured error taxonomy for codoc.

Every codoc-originated error carries a short code, a human message, and an
optional remediation hint.  CLI commands catch CodocError and print the code +
remediation instead of a raw traceback.
"""

from __future__ import annotations


class CodocError(Exception):
    """Base class for all codoc errors."""

    code: str = "CODOC_ERROR"
    remediation: str = ""

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict = details or {}

    def __str__(self) -> str:
        base = f"[{self.code}] {self.message}"
        if self.remediation:
            base += f"\n  fix: {self.remediation}"
        return base


class LlmCallFailed(CodocError):
    code = "LLM_CALL_FAILED"
    remediation = "Check OPENAI_API_KEY, network connectivity, and model name in CODOC_MODEL."


class LlmInvalidJson(CodocError):
    code = "LLM_INVALID_JSON"
    remediation = "Set CODOC_LOG_PROMPTS=1 to inspect the raw LLM response."


class LlmSchemaValidationFailed(CodocError):
    code = "LLM_SCHEMA_VALIDATION_FAILED"
    remediation = "Set CODOC_LOG_PROMPTS=1 to inspect the raw LLM response."


class BindingResolutionFailed(CodocError):
    code = "BINDING_RESOLUTION_FAILED"
    remediation = "Run 'codoc health' to reconcile bindings, or 'codoc show <slug>' to inspect."


class ConcurrentLockHeld(CodocError):
    code = "CONCURRENT_LOCK_HELD"
    remediation = "Another codoc process is running. Wait for it to finish, or remove .codoc/codoc.lock."


class StaleBuffer(CodocError):
    code = "STALE_BUFFER"
    remediation = "Run 'codoc projection render' to refresh .codoc/tree/."


class SchemaVersionMismatch(CodocError):
    code = "SCHEMA_VERSION_MISMATCH"
    remediation = "Run 'codoc init' to archive the old .codoc/ and start with the v2 schema."


class NotInitialized(CodocError):
    code = "NOT_INITIALIZED"
    remediation = "Run 'codoc init' in your repository root."


class BootstrapFailed(CodocError):
    code = "BOOTSTRAP_FAILED"
    remediation = "Run 'codoc bootstrap' manually after fixing the issue."


class ReflectFailed(CodocError):
    code = "REFLECT_FAILED"
    remediation = "Check git status and run 'codoc reflect' again."


class ProjectionParseError(CodocError):
    code = "PROJECTION_PARSE_ERROR"
    remediation = "Fix the syntax error in .codoc/tree/_index.codoc, then run 'codoc projection sync'."


class IgnoredPath(CodocError):
    code = "IGNORED_PATH"
    remediation = "Check .codocignore or .gitignore rules."
