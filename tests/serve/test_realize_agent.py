"""The ENFORCED sandbox for hub realization (codoc/serve/realize_agent.py).

The security-critical piece is ``realize_tool_policy`` — the single decision point
that binds the sandbox (no Bash, scoped edits, no secret reads) AND the consult
SSRF gate onto one tool call. Plus the git change-detection and the fail-safe.
"""
from __future__ import annotations

from codoc.serve.realize_agent import (
    HUB_ALLOWED_TOOLS,
    build_directive_prompt,
    changed_files,
    consult_allowlist_from_env,
    make_sandboxed_agent,
    realize_tool_policy,
)


def _resolve_public(host):
    return ["93.184.216.34"]  # a public IP


def _policy(scope=None, allow=("docs.example",), resolve=_resolve_public):
    return realize_tool_policy(scope, consult_allowlist=frozenset(allow), resolve=resolve)


def test_bash_is_always_denied():
    ok, reason = _policy()("Bash", {"command": "curl evil"})
    assert ok is False
    assert "Bash" in reason


def test_edit_outside_scope_denied_inside_scope_allowed():
    p = _policy(scope=["codoc/auth/session.py"])
    assert p("Edit", {"file_path": "codoc/auth/session.py"})[0] is True
    assert p("Edit", {"file_path": ".github/workflows/ci.yml"})[0] is False
    assert p("Edit", {"file_path": "codoc/other.py"})[0] is False


def test_reading_secrets_denied():
    ok, _ = _policy()("Read", {"file_path": ".env"})
    assert ok is False


def test_webfetch_gated_by_consult_allowlist():
    p = _policy(allow=("docs.example",))
    # allowed host, public IP → permitted
    assert p("WebFetch", {"url": "https://docs.example/spec"})[0] is True
    # host not in the allowlist → denied
    assert p("WebFetch", {"url": "https://evil.test/x"})[0] is False
    # http (not https) → denied
    assert p("WebFetch", {"url": "http://docs.example/x"})[0] is False


def test_webfetch_denied_when_host_resolves_to_private_ip():
    p = _policy(allow=("internal.example",), resolve=lambda h: ["169.254.169.254"])
    ok, reason = p("WebFetch", {"url": "https://internal.example/latest/meta-data"})
    assert ok is False
    assert "consult blocked" in reason


def test_webfetch_denied_by_default_empty_allowlist():
    p = realize_tool_policy(None, consult_allowlist=consult_allowlist_from_env({}),
                            resolve=_resolve_public)
    assert p("WebFetch", {"url": "https://docs.example/spec"})[0] is False


def test_hub_allowed_tools_excludes_bash_includes_webfetch():
    assert "Bash" not in HUB_ALLOWED_TOOLS
    assert "WebFetch" in HUB_ALLOWED_TOOLS
    assert "Edit" in HUB_ALLOWED_TOOLS and "Write" in HUB_ALLOWED_TOOLS


def test_changed_files_parses_git_porcelain():
    porcelain = " M codoc/a.py\n?? codoc/new.py\nR  old.py -> codoc/renamed.py\n"
    files = changed_files("/wt", run_capture=lambda argv, cwd: porcelain)
    assert files == ["codoc/a.py", "codoc/new.py", "codoc/renamed.py"]


def test_consult_allowlist_from_env_parsing():
    al = consult_allowlist_from_env({"CODOC_CONSULT_ALLOWLIST": "Docs.Example, api.foo "})
    assert al == frozenset({"docs.example", "api.foo"})
    assert consult_allowlist_from_env({}) == frozenset()


def test_prompt_states_scope_and_restrictions():
    prompt = build_directive_prompt({"text": "Add a login route"}, ["codoc/auth.py"])
    assert "Add a login route" in prompt
    assert "codoc/auth.py" in prompt
    assert "shell" in prompt.lower()


def test_agent_is_fail_safe_without_sdk(monkeypatch):
    # With the SDK unavailable, the agent must run NOTHING and report no changes —
    # never fall back to an unsandboxed run. We prove it by asserting the git
    # change-detection is never consulted (no SDK → returns [] before that).
    called = {"capture": 0}

    def capture(argv, cwd):
        called["capture"] += 1
        return ""

    agent = make_sandboxed_agent(run_capture=capture)
    # Force the SDK path to report "cannot sandbox".
    import codoc.serve.realize_agent as ra
    monkeypatch.setattr(ra, "_run_sandboxed_sdk", lambda **_k: False)
    assert agent({"id": "d-1", "text": "x"}, "/tmp/wt", scope=None) == []
    assert called["capture"] == 0
