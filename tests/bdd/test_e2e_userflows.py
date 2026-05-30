"""Real-LLM E2E userflow — runs ``e2e_report`` in an isolated process.

This is the non-deterministic counterpart to the deterministic BDD suites: it
drives bootstrap + Loop A with the real index and real LLM, then asserts only the
invariants that must hold regardless of the model's exact choices. The full
position report is printed (visible with ``pytest -s``) for manual inspection of
*where* each change landed — which the user is expected to eyeball, since the LLM
may legitimately attach, propose, refresh, or amend.

It runs in a subprocess because cocoindex's index is a per-process singleton and
``tests/loop/test_end_to_end.py`` already builds one in the main pytest process.
Skipped when no ``OPENAI_API_KEY`` is configured.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from codoc.config import get_llm_config

_REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not get_llm_config().api_key, reason="no OPENAI_API_KEY configured"
)


def test_real_llm_userflow_report(capsys):
    proc = subprocess.run(
        [sys.executable, "-m", "tests.bdd.e2e_report"],
        capture_output=True, text=True, cwd=str(_REPO_ROOT), timeout=900,
    )
    report = proc.stdout + ("\n[stderr]\n" + proc.stderr if proc.stderr.strip() else "")
    with capsys.disabled():
        print("\n" + report)

    assert proc.returncode == 0, f"E2E invariants failed (exit {proc.returncode}):\n{report}"
    assert "INVARIANTS: ALL PASS" in report, f"missing all-pass marker:\n{report}"
