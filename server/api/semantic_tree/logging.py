"""PipelineLogger and CODENAV one-line logs for semantic tree pipeline."""

import logging
import time
from contextlib import contextmanager
from typing import Any, Optional

logger = logging.getLogger("codenav.pipeline")
# One-line human-readable logs for testers (sync/tree_edit/apply_tree_edit)
codenav_log = logging.getLogger("codenav")


class PipelineLogger:
    """
    Logs pipeline stages with [STAGE:START], [STAGE:OK], [STAGE:FAIL] and timing.
    Use stage() as context manager; call summary() at end for total + per-stage breakdown.
    """

    def __init__(self) -> None:
        self._timings: dict[str, float] = {}
        self._current_stage: Optional[str] = None
        self._start: float = 0.0

    @contextmanager
    def stage(self, name: str, **kwargs: Any):
        """Context manager for a pipeline stage. Logs start/ok or fail with elapsed time."""
        self._current_stage = name
        self._start = time.perf_counter()
        extra = " | ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
        logger.info("[STAGE:START] %s%s", name, (" | " + extra) if extra else "")
        try:
            yield
            elapsed = time.perf_counter() - self._start
            self._timings[name] = elapsed
            logger.info("[STAGE:OK]   %s | elapsed=%.2fs%s", name, elapsed, (" | " + extra) if extra else "")
        except Exception as e:
            elapsed = time.perf_counter() - self._start
            logger.exception("[STAGE:FAIL] %s | elapsed=%.2fs | error=%s", name, elapsed, e)
            raise
        finally:
            self._current_stage = None

    def summary(self) -> dict[str, float]:
        """Return timing breakdown and log [PIPELINE:SUMMARY]. Does not clear timings."""
        total = sum(self._timings.values())
        parts = " | ".join(f"{k}={v:.2f}s" for k, v in self._timings.items())
        logger.info("[PIPELINE:SUMMARY] total=%.2fs | %s", total, parts)
        return {"total": total, **self._timings}


def log_sync(mode: str, entities: int, delta: dict | None, index_action: str, semantic_action: str) -> None:
    """One-line [CODENAV] SYNC summary for testers. Use force_full=false for fast incremental (no full reindex)."""
    if mode == "full":
        codenav_log.info(
            "[CODENAV] SYNC | mode=full | entities=%s | index=%s | semantic=%s",
            entities, index_action, semantic_action,
        )
    elif mode == "patch":
        d = delta or {}
        codenav_log.info(
            "[CODENAV] SYNC | mode=patch | delta +%s -%s ~%s | index=%s | semantic=%s (no embedding)",
            d.get("added", 0), d.get("removed", 0), d.get("unchanged", 0),
            index_action, semantic_action,
        )
    else:
        d = delta or {}
        codenav_log.info(
            "[CODENAV] SYNC | mode=incremental | delta +%s -%s ~%s | index=%s | semantic=%s (fast: no full reindex)",
            d.get("added", 0), d.get("removed", 0), d.get("unchanged", 0),
            index_action, semantic_action,
        )


def _op_name(item: Any) -> str:
    return item.get("op", "") if isinstance(item, dict) else getattr(item, "op", "?")


def _targets_list(item: Any) -> list:
    if isinstance(item, dict):
        return item.get("targets", [])
    return getattr(item, "targets", [])


def _target_str(t: Any) -> str:
    fpath = t.get("fpath", "") if isinstance(t, dict) else getattr(t, "fpath", "") or ""
    entity = t.get("entity_name", "") if isinstance(t, dict) else getattr(t, "entity_name", "") or ""
    return f"{fpath}:{entity}" if fpath and entity else (fpath or entity or "")


def log_tree_edit(ops: list) -> None:
    """One-line [CODENAV] TREE_EDIT summary: operation types and code targets."""
    by_op: dict[str, int] = {}
    for item in ops:
        op = _op_name(item)
        by_op[op] = by_op.get(op, 0) + 1
    op_parts = " ".join(f"{k}({v})" for k, v in sorted(by_op.items()))
    targets = []
    for item in ops:
        for t in _targets_list(item):
            s = _target_str(t)
            if s:
                targets.append(s)
    target_str = ", ".join(targets[:8]) + (" ..." if len(targets) > 8 else "") if targets else "none"
    codenav_log.info("[CODENAV] TREE_EDIT | ops: %s | targets: %s", op_parts, target_str)


def log_apply_tree_edit(applied: bool, tree_version: int) -> None:
    """One-line [CODENAV] APPLY_TREE_EDIT summary."""
    codenav_log.info(
        "[CODENAV] APPLY_TREE_EDIT | persisted=%s | tree_version=%s",
        applied, tree_version,
    )
