"""Which model codoc's passes run on, and the clients that call it.

**Choosing.** ``get_llm_config`` reads the environment, after filling the
variables it leaves unset from a ``.env`` (``_fill_from_dotenv``). The provider
is ``CODOC_PROVIDER`` when set; otherwise ``openai`` when ``OPENAI_API_KEY`` is
set, else ``anthropic`` when ``ANTHROPIC_API_KEY`` is set, else ``claude``.
``fast_llm_config`` picks the cheaper model for the high-volume passes.

**Calling.** ``complete`` sends one prompt to OpenAI, Anthropic, Ollama, or
Claude Code. Claude Code (provider ``claude``, the default) needs no API key: it
runs the ``claude`` CLI in headless JSON mode under the user's Claude login.
``spent`` totals what the calls reported they cost.

Environment variables:
    CODOC_PROVIDER          LLM provider: "openai" | "anthropic" | "ollama" | "claude"
                            (default: inferred from which key is present, else "claude")
    CODOC_MODEL             Model name  (per-provider default: gpt-5.4-mini / claude-sonnet-4-6 / sonnet)
    CODOC_MODEL_FAST        Model for the high-volume extraction calls only
    OPENAI_API_KEY          API key for OpenAI (required when provider=openai)
    ANTHROPIC_API_KEY       API key for Anthropic (required when provider=anthropic)
    CODOC_BASE_URL          Override base URL (e.g. for local OpenAI-compatible servers)
    CODOC_TEMPERATURE       Float  (default 0.2). Set it empty to send no
                            temperature at all, which some reasoning models
                            require: they accept only their own default and
                            answer 400 to every value, including it.
    CODOC_MAX_TOKENS        Int    (default 16000)
    CODOC_REASONING_EFFORT  "low" | "medium" | "high"  (sent only when set)
    CODOC_VERBOSITY         "low" | "medium" | "high"  (sent only when set)
    CODOC_LLM_TIMEOUT       Seconds before a network call is abandoned (default 300)
    CODOC_LOG_PROMPTS       Set to "1" to log prompt+response to stderr
"""

from __future__ import annotations

import functools
import os
import sys
import threading
from dataclasses import dataclass, replace

from dotenv import dotenv_values, find_dotenv

# A repository's .env may not redirect codoc's API calls (a base URL would
# send the key elsewhere) or turn on prompt logging. Set these in the shell.
_UNTRUSTED_FROM_DOTENV = frozenset({
    "CODOC_BASE_URL", "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "CODOC_LOG_PROMPTS",
})


@functools.cache
def _fill_from_dotenv() -> None:
    """Set each variable the environment leaves unset from the ``.env`` nearest
    the working directory, once per process.

    codoc runs from the repository (the daemon, the hooks, the MCP server), and
    the editor's setup writes the chosen provider into the repository's
    ``.env``. ``usecwd`` matters: without it ``find_dotenv`` starts from the
    calling file, which is in site-packages. The file only fills gaps, so a
    checked-in ``.env`` never overrides the shell, and the variables in
    ``_UNTRUSTED_FROM_DOTENV`` are never taken from it.
    """
    path = find_dotenv(usecwd=True)
    if path:
        for k, v in dotenv_values(path).items():
            if v is not None and k not in _UNTRUSTED_FROM_DOTENV:
                os.environ.setdefault(k, v)


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


@dataclass
class LLMConfig:
    provider: str  # "openai" | "anthropic" | "ollama" | "claude"
    model: str
    api_key: str | None = None
    base_url: str | None = None
    # None sends no temperature, for a model that accepts only its own default.
    temperature: float | None = 0.2
    # Reasoning models spend completion budget on hidden reasoning, so the
    # budget must comfortably exceed the visible JSON.
    max_tokens: int = 16000
    # How hard a reasoning model thinks, and how much prose it returns. Sent only
    # when set, because a model that does not know the field rejects the call.
    reasoning_effort: str | None = None
    verbosity: str | None = None
    timeout: float = 300.0       # seconds before a network call is abandoned


# Each provider's model when CODOC_MODEL is unset. The ``claude`` CLI takes an
# alias; the Anthropic API needs a model id.
_DEFAULT_MODELS = {
    "openai": "gpt-5.4-mini",
    "anthropic": "claude-sonnet-4-6",
    "claude": "sonnet",
}

# The fast tier, for the passes that run most often and return short JSON op
# lists against explicit rules: the reflect pass, the bootstrap's brief and
# per-file passes. The organization and translate passes stay on the standard
# tier. ``CODOC_MODEL`` overrides both tiers; ``CODOC_MODEL_FAST`` only this one.
_FAST_MODELS = {
    "openai": "gpt-5.4-mini",
    "anthropic": "claude-haiku-4-5",
    "claude": "haiku",
}


def fast_llm_config(base: LLMConfig | None = None) -> LLMConfig:
    """The config for the fast tier: ``CODOC_MODEL_FAST``, else ``CODOC_MODEL``
    (a model pinned for everything), else the provider's fast default."""
    cfg = base or get_llm_config()
    fast = os.environ.get("CODOC_MODEL_FAST")
    if fast:
        if not _model_fits_provider(cfg.provider, fast):
            return cfg
        return replace(cfg, model=fast)
    if os.environ.get("CODOC_MODEL"):
        return cfg  # explicit global pin wins
    fast_default = _FAST_MODELS.get(cfg.provider)
    if fast_default is None:
        return cfg
    return replace(cfg, model=fast_default)


def _is_claude_family_model(model: str) -> bool:
    """Whether a model name belongs to the Claude family, which runs on the
    ``claude`` CLI and the Anthropic API and not on OpenAI."""
    m = model.lower()
    return any(tag in m for tag in ("sonnet", "opus", "haiku", "claude"))


def _model_fits_provider(provider: str, model: str) -> bool:
    """Whether ``model`` can run on ``provider``. A ``CODOC_MODEL=gpt-…`` exported
    for another tool, met on the ``claude`` provider, does not fit, and the
    caller falls back to the provider's default."""
    if provider in ("claude", "anthropic"):
        return _is_claude_family_model(model)
    if provider == "openai":
        return not _is_claude_family_model(model)
    return True  # ollama / unknown: trust the user's explicit model


def _temperature_from_env() -> float | None:
    """The temperature to send, or None to send none.

    ``CODOC_TEMPERATURE=`` (set but empty) means none. Unset means the default
    of 0.2.
    """
    raw = os.environ.get("CODOC_TEMPERATURE")
    if raw is None:
        return 0.2
    raw = raw.strip()
    if raw == "" or raw.lower() in ("none", "off", "default"):
        return None
    try:
        return float(raw)
    except ValueError:
        return 0.2


def _resolve_provider() -> str:
    """``CODOC_PROVIDER`` when set (the editor's setup writes it); else the
    provider whose key is set; else ``claude``, which needs no key."""
    explicit = os.environ.get("CODOC_PROVIDER")
    if explicit:
        return explicit
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "claude"


def get_llm_config() -> LLMConfig:
    """The config the environment describes, with a ``.env`` filling its gaps.
    The model and the key follow the provider."""
    _fill_from_dotenv()
    provider = _resolve_provider()
    default_model = _DEFAULT_MODELS.get(provider, "gpt-5.4-mini")
    model = os.environ.get("CODOC_MODEL") or default_model
    if not _model_fits_provider(provider, model):
        # A stray cross-family CODOC_MODEL can't run on this provider; ignore it.
        model = default_model
    key_var = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(provider)
    return LLMConfig(
        provider=provider,
        model=model,
        api_key=os.environ.get(key_var) if key_var else None,
        base_url=os.environ.get("CODOC_BASE_URL"),
        temperature=_temperature_from_env(),
        max_tokens=int(os.environ.get("CODOC_MAX_TOKENS", "16000")),
        reasoning_effort=os.environ.get("CODOC_REASONING_EFFORT") or None,
        verbosity=os.environ.get("CODOC_VERBOSITY") or None,
        timeout=float(os.environ.get("CODOC_LLM_TIMEOUT", "300")),
    )


def complete(
    prompt: str,
    config: LLMConfig | None = None,
    *,
    prefix_parts: list[str] | None = None,
) -> str:
    """Call the configured LLM and return the raw response string.

    ``prefix_parts`` are the stable prompt segments (the instructions, the
    feature titles) that precede the per-call ``prompt``, most stable first,
    so a provider can cache them: Anthropic gets them as system blocks with
    ``cache_control`` breakpoints, OpenAI caches a repeated prefix on its own,
    and the other providers concatenate them. Every provider shows the model
    ``prefix_parts`` then ``prompt``, in order.
    """
    if config is None:
        config = get_llm_config()
    parts = [p for p in (prefix_parts or []) if p]

    log_prompts = os.environ.get("CODOC_LOG_PROMPTS", "0") == "1"
    if log_prompts:
        joined = "\n\n".join(parts + [prompt])
        print(f"[CODOC] PROMPT:\n{joined}", file=sys.stderr)

    if config.provider == "openai":
        response_text = _complete_openai(prompt, config, parts)
    elif config.provider == "ollama":
        response_text = _complete_ollama(prompt, config, parts)
    elif config.provider == "anthropic":
        response_text = _complete_anthropic(prompt, config, parts)
    elif config.provider in ("claude", "claude-code"):
        response_text, cost = _complete_claude(prompt, config, parts)
        if cost is not None:
            with _spent_lock:
                _spent[0] += 1
                _spent[1] += cost
    else:
        raise ValueError(f"Unknown LLM provider: {config.provider!r}")

    if log_prompts:
        print(f"[CODOC] RESPONSE:\n{response_text}", file=sys.stderr)

    return response_text


# What this process's calls reported they cost: (calls that reported one, US
# dollars). Only the ``claude`` CLI reports a price. The APIs report tokens, and
# a price table to turn tokens into dollars would go stale, so their calls add
# nothing here.
_spent = [0, 0.0]
_spent_lock = threading.Lock()


def spent() -> tuple[int, float]:
    """``(calls that reported a cost, their total in US dollars)`` in this process."""
    with _spent_lock:
        return _spent[0], _spent[1]


def cost_since(mark: tuple[int, float]) -> float | None:
    """What the calls since ``spent()`` returned ``mark`` cost in US dollars,
    or None when none of them reported a cost."""
    n, usd = spent()
    return usd - mark[1] if n > mark[0] else None


def _joined(parts: list[str], prompt: str) -> str:
    return "\n\n".join(parts + [prompt]) if parts else prompt


# Every network path has a timeout (``LLMConfig.timeout``) and bounded retries:
# a wedged connection would otherwise hang a loop pass and the daemon behind it.
_LLM_RETRIES = 2


# Models the API said accept only their default temperature; see the retry in
# _complete_openai.
_REFUSES_TEMPERATURE: set[str] = set()


def _complete_openai(prompt: str, config: LLMConfig, parts: list[str]) -> str:
    try:
        import openai
    except ImportError as exc:
        raise ImportError("openai package is required for provider='openai'. Run: pip install openai") from exc

    kwargs: dict = {"timeout": config.timeout, "max_retries": _LLM_RETRIES}
    if config.api_key is not None:
        kwargs["api_key"] = config.api_key
    if config.base_url is not None:
        kwargs["base_url"] = config.base_url

    client = openai.OpenAI(**kwargs)
    # The stable prefix and the per-call tail in one user message: OpenAI's
    # automatic prefix caching (from 1024 tokens) needs byte-identical leading
    # content, which prefix_parts are. prompt_cache_key routes this workspace's
    # requests to the same cache shard.
    extra: dict = {}
    # Only against the stock OpenAI endpoint: strictly-validating compatible
    # servers (some Azure API versions, local proxies behind CODOC_BASE_URL)
    # reject unknown body fields with a 400 on every call.
    if config.base_url is None:
        try:
            import hashlib

            extra["prompt_cache_key"] = "codoc-" + hashlib.sha1(
                os.getcwd().encode(), usedforsecurity=False
            ).hexdigest()[:16]
        except Exception:  # noqa: BLE001 (cache hint only)
            pass
    def _send(budget: int):
        kwargs = {
            "model": config.model,
            "messages": [{"role": "user", "content": _joined(parts, prompt)}],
            "max_completion_tokens": budget,
            "extra_body": extra or None,
        }
        if config.temperature is not None and config.model not in _REFUSES_TEMPERATURE:
            kwargs["temperature"] = config.temperature
        if config.reasoning_effort:
            kwargs["reasoning_effort"] = config.reasoning_effort
        if config.verbosity:
            kwargs["verbosity"] = config.verbosity
        try:
            return client.chat.completions.create(**kwargs)
        except openai.BadRequestError as exc:
            # Some reasoning models accept only the default temperature and 400
            # on any other. Learn it from the API rather than from a list of
            # model names, and remember it, so it costs one refused call per
            # model per process.
            if "temperature" not in str(exc) or "temperature" not in kwargs:
                raise
            _REFUSES_TEMPERATURE.add(config.model)
            kwargs.pop("temperature")
            return client.chat.completions.create(**kwargs)

    # A reasoning model spends completion budget on hidden reasoning. When the
    # visible answer is cut off (finish_reason "length"), retry once with a
    # larger budget rather than return half a JSON object.
    budget = config.max_tokens
    for attempt in range(2):
        response = _send(budget)
        choice = response.choices[0]
        content = choice.message.content or ""
        if choice.finish_reason != "length" or attempt == 1:
            return content
        budget = min(budget * 3, 64000)
    return content


def _complete_ollama(prompt: str, config: LLMConfig, parts: list[str]) -> str:
    try:
        import ollama
    except ImportError as exc:
        raise ImportError("ollama package is required for provider='ollama'. Run: pip install ollama") from exc

    response = ollama.chat(
        model=config.model,
        messages=[{"role": "user", "content": _joined(parts, prompt)}],
        options=({"num_predict": config.max_tokens}
                 | ({} if config.temperature is None else {"temperature": config.temperature})),
    )
    return response["message"]["content"]


def _complete_anthropic(prompt: str, config: LLMConfig, parts: list[str]) -> str:
    """Complete through the Anthropic API with ``ANTHROPIC_API_KEY``. The model
    is an API id (``claude-sonnet-4-6``), not a CLI alias.

    The first two prefix parts become system blocks with a ``cache_control``
    breakpoint (the API allows four), so consecutive passes that resend the
    same instructions and titles read them from the cache. Below the model's
    minimum cacheable length the API ignores the marker.
    """
    try:
        import anthropic
    except ImportError as exc:
        raise ImportError(
            "provider='anthropic' needs the anthropic package. Run: pip install 'codoc[anthropic]'"
        ) from exc

    kwargs: dict = {"timeout": config.timeout, "max_retries": _LLM_RETRIES}
    if config.api_key is not None:
        kwargs["api_key"] = config.api_key
    if config.base_url is not None:
        kwargs["base_url"] = config.base_url

    client = anthropic.Anthropic(**kwargs)
    create_kwargs: dict = {
        "model": config.model,
        "max_tokens": config.max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    # Omitted rather than sent as None, which every client rejects. A caller asks
    # for the model's own default by setting CODOC_TEMPERATURE empty.
    if config.temperature is not None:
        create_kwargs["temperature"] = config.temperature
    if parts:
        create_kwargs["system"] = [
            {"type": "text", "text": part, "cache_control": {"type": "ephemeral"}}
            for part in parts[:2]
        ] + [{"type": "text", "text": part} for part in parts[2:]]
    message = client.messages.create(**create_kwargs)
    # The text blocks of the response, concatenated; any other block is skipped.
    return "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )


# The system prompt every ``claude`` completion runs under, in place of Claude
# Code's agent prompt. The agent prompt, tool schemas and user config add about
# 37K tokens to every call, none of them used here; without them a completion
# is 1K to 2K tokens, and the stable prefix (this preamble and prefix_parts) is
# read from the prompt cache across spawns.
_CLAUDE_MIN_SYSTEM = (
    "You are codoc's structured code-analysis engine. Follow the user message "
    "exactly and return output only in the format it requests."
)


def _complete_claude(prompt: str, config: LLMConfig, parts: list[str]) -> tuple[str, float | None]:
    """Complete through the ``claude`` CLI in headless JSON mode, under the
    user's Claude login, with no API key.

    The CLI and not the ``anthropic`` SDK, because the OAuth token Claude Code
    stores is not an API key; only the ``claude`` binary can use it.

    * No ``--bare``: bare mode skips the keychain read and so demands a key.
    * No ``ANTHROPIC_API_KEY`` in the child's environment: when present it
      overrides the login and bills another account.
    * A temporary directory as the working directory, so the repository's own
      hooks, MCP server and CLAUDE.md do not load (they are heavy, and a hook
      could start codoc again).
    * ``--system-prompt-file`` replaces the agent prompt with
      ``_CLAUDE_MIN_SYSTEM`` and the prefix parts, and ``--disallowedTools "*"``
      drops every tool schema.

    Returns the model's text, the JSON envelope's ``result``, and the cost it
    reports (``total_cost_usd``), None when it reports none.
    """
    import json as _json
    import shutil
    import subprocess
    import tempfile

    claude = shutil.which("claude")
    if claude is None:
        raise RuntimeError(
            "provider='claude' needs the `claude` CLI on PATH "
            "(install Claude Code, or set CODOC_PROVIDER=openai)."
        )

    # Child env minus ANTHROPIC_API_KEY so the subscription login wins.
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    # The model rides in argv; reject a flag-shaped value (e.g. from a malicious
    # .env) that the CLI parser could re-lex into options.
    if config.model and config.model.startswith("-"):
        raise ValueError(f"Refusing a flag-shaped CODOC_MODEL: {config.model!r}")

    # The system prompt carries repo-derived content (feature titles), so it goes
    # through a file, never argv: argv is visible in `ps` and a crafted value
    # could be re-lexed into CLI options.
    system_text = "\n\n".join([_CLAUDE_MIN_SYSTEM] + parts)
    cmd = [
        claude, "-p",
        "--output-format", "json",
        "--max-turns", "1",
        "--allowedTools", "",     # pure completion: no tools, no agent loop
        "--disallowedTools", "*",  # and no tool schemas in the prompt at all
    ]
    if config.model:
        cmd += ["--model", config.model]

    # The prompt holds repository content, which anyone with write access to a
    # shared repository controls. It goes on stdin, never argv: a flag-shaped
    # prompt cannot be read as CLI options (so the tool and turn caps hold), and
    # source snippets do not appear in `ps`.
    sf = tempfile.NamedTemporaryFile(
        "w", suffix=".txt", prefix="codoc-sys-", delete=False
    )
    system_file = sf.name
    try:
        # Everything from first write to subprocess exit is covered by the
        # unlink below, because a failed write (disk full) must not leak the file.
        with sf:
            sf.write(system_text)
        proc = subprocess.run(
            cmd + ["--system-prompt-file", system_file],
            input=prompt, capture_output=True, text=True,
            cwd=tempfile.gettempdir(), env=env, timeout=config.timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"`claude -p` timed out after {config.timeout:.0f}s"
        ) from exc
    finally:
        try:
            os.unlink(system_file)
        except OSError:
            pass
    if proc.returncode != 0:
        raise RuntimeError(
            f"`claude -p` failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:300]}"
        )
    try:
        payload = _json.loads(proc.stdout)
    except _json.JSONDecodeError as exc:
        raise RuntimeError(
            f"`claude -p` returned non-JSON output: {proc.stdout[:300]!r}"
        ) from exc
    # The headless JSON envelope reports outcome via subtype/is_error; a billing
    # or rate-limit subtype must surface, not return half an answer.
    if payload.get("is_error") or payload.get("subtype") not in (None, "success"):
        raise RuntimeError(
            f"`claude -p` did not succeed (subtype={payload.get('subtype')!r}): "
            f"{str(payload.get('result') or '')[:300]}"
        )
    cost = payload.get("total_cost_usd")
    return str(payload.get("result", "")), float(cost) if isinstance(cost, (int, float)) else None
