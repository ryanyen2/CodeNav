"""PipelineLogger: structured stage timing and summary for semantic tree pipeline."""

import logging
import time
from contextlib import contextmanager
from typing import Any, Optional

logger = logging.getLogger("codenav.pipeline")


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
