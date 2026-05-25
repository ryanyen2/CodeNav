"""End-to-end: real cocoindex index + real LLM (no mocks).

Skips only if no OPENAI_API_KEY is configured.

ONE index per process: cocoindex's App/lifespan is a module-level singleton, so a
process may only drive one ``codoc_dir``. This single test therefore exercises
the whole system on one repo — bootstrap, then Loop A across added / modified /
removed code — rather than splitting into separate index-creating tests (which
would collide in a shared process).
"""
from __future__ import annotations

import pytest

from codoc.config import get_llm_config
from codoc.loop.bootstrap import run_init
from codoc.loop.loop_a import run_loop_a
from codoc.store.db import open_store

pytestmark = pytest.mark.skipif(
    not get_llm_config().api_key, reason="no OPENAI_API_KEY configured"
)

AUTH = '''\
def create_session(user):
    """Create a new session token for a user."""
    return f"token-{user}"


def login(username, password):
    """Authenticate a user and return a session."""
    if password == "secret":
        return create_session(username)
    return None


def logout(token):
    """Invalidate a session token."""
    return True
'''

MATH = '''\
def add(a, b):
    """Add two numbers."""
    return a + b


def subtract(a, b):
    """Subtract b from a."""
    return a - b
'''


def test_real_end_to_end(tmp_path, capsys):
    root_p = tmp_path / "repo"
    root_p.mkdir()
    (root_p / "auth.py").write_text(AUTH)
    (root_p / "math_utils.py").write_text(MATH)
    root = str(root_p)
    codoc_dir = str(root_p / ".codoc")

    # --- bootstrap (real index + real LLM) ---
    res = run_init(root)
    store = open_store(codoc_dir)
    feats = store.list_features()
    bound = {b.symbol_path for b in store.all_bindings()}
    with capsys.disabled():
        print(f"\n[bootstrap] {res.summary()}")
        for f in feats:
            print(f"  - {f.title}  ({len(store.bindings_for_feature(f.id))} bindings)")
    store.close()

    assert feats, "bootstrap produced no features"
    assert any("auth.py::login" in s for s in bound)
    assert any("math_utils.py::add" in s for s in bound)
    titles = [f.title for f in feats]
    assert len(titles) == len(set(titles)), f"duplicate titles: {titles}"

    # --- Loop A: ADD a new file → attached or proposed as a new node ---
    (root_p / "payments.py").write_text(
        'def charge(card, amount):\n    """Charge a credit card."""\n    return {"ok": True}\n'
    )
    ra = run_loop_a(root, codoc_dir, file_scope={"payments.py"})
    with capsys.disabled():
        print(f"[loop_a +payments.py] {ra.summary()}")
    store = open_store(codoc_dir)
    charge_bound = store.binding_at("payments.py", "payments.py::charge") is not None
    proposed_charge = any(
        ("payments.py", "payments.py::charge") in e.op.bindings for e in store.pending_events()
    )
    store.close()
    assert charge_bound or proposed_charge, "added code neither attached nor proposed"

    # --- Loop A: MODIFY an existing bound function → auto REFRESH ---
    store = open_store(codoc_dir)
    before = store.binding_at("auth.py", "auth.py::login")
    store.close()
    (root_p / "auth.py").write_text(AUTH.replace('"secret"', '"hunter2"'))
    ra2 = run_loop_a(root, codoc_dir, file_scope={"auth.py"})
    with capsys.disabled():
        print(f"[loop_a ~auth.login] {ra2.summary()}")
    store = open_store(codoc_dir)
    after = store.binding_at("auth.py", "auth.py::login")
    store.close()
    assert after is not None and after.fingerprint != before.fingerprint, "fingerprint not refreshed"

    # --- Loop A: REMOVE a file → bindings detached automatically ---
    (root_p / "math_utils.py").unlink()
    ra3 = run_loop_a(root, codoc_dir, file_scope={"math_utils.py"})
    with capsys.disabled():
        print(f"[loop_a -math_utils.py] {ra3.summary()}")
    store = open_store(codoc_dir)
    still_bound = store.binding_at("math_utils.py", "math_utils.py::add")
    store.close()
    assert still_bound is None, "removed code's binding was not detached"
