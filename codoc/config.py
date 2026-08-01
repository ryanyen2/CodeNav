"""
codoc.config — LLM and embedder configuration, env-var driven.

Supports OpenAI, Anthropic, Ollama, and Claude Code for completions; OpenAI and
sentence-transformers for embeddings. No adalflow dependency.

The default, keyless path is **Claude Code** (provider ``claude``): it reuses the
user's *existing* Claude login by shelling out to the ``claude`` CLI in headless
JSON mode — no separate API key. This is what lets the VS Code extension offer a
zero-key setup: codoc's reflection calls ride on the same auth Claude Code already
resolved. Users who prefer a managed API can instead set an OpenAI key (provider
``openai``) or an Anthropic key (provider ``anthropic``).

Provider resolution (``get_llm_config``) when ``CODOC_PROVIDER`` is unset:
``OPENAI_API_KEY`` present → ``openai``; else ``ANTHROPIC_API_KEY`` present →
``anthropic``; else → ``claude`` (keyless Claude Code). An explicit
``CODOC_PROVIDER`` always wins. So a fresh install with no key "just works" via
Claude Code, and never crashes demanding an OpenAI key.

Environment variables:
    CODOC_PROVIDER          LLM provider: "openai" | "anthropic" | "ollama" | "claude"
                            (default: inferred from which key is present, else "claude")
    CODOC_MODEL             Model name  (per-provider default: gpt-5.4-mini / claude-sonnet-4-6 / sonnet)
    OPENAI_API_KEY          API key for OpenAI (required when provider=openai)
    ANTHROPIC_API_KEY       API key for Anthropic (required when provider=anthropic)
    CODOC_BASE_URL          Override base URL (e.g. for local OpenAI-compatible servers)
    CODOC_TEMPERATURE       Float  (default 0.2)
    CODOC_MAX_TOKENS        Int    (default 16000)
    CODOC_LOG_PROMPTS       Set to "1" to log prompt+response to stderr

    CODOC_EMBEDDER_PROVIDER Embedder provider: "openai" | "sentence-transformers"  (default "sentence-transformers")
    CODOC_EMBEDDER_MODEL    Embedding model name  (default "all-MiniLM-L6-v2")
"""

from __future__ import annotations

import os
import sys

from pydantic import BaseModel
from dotenv import dotenv_values, find_dotenv

# Load the repo's .env from the CURRENT WORKING DIRECTORY, not from this module's
# install location. Bare load_dotenv()/find_dotenv() walk up from the *caller's
# file* (site-packages/codoc/config.py for a uv-tool install), so they never find
# the project .env and the user's CODOC_PROVIDER choice is silently ignored.
# codoc is always run with cwd = the repo (the daemon, the CC hooks, `codoc init
# --root <repo>` all set it), so usecwd=True is the correct, robust anchor.
#
# codoc runs against ARBITRARY user repos, so the repo's .env is treated as a
# FALLBACK only — it fills in config the user has not already set in their real
# environment, and never OVERRIDES the shell. Overriding would let any checked-in
# .env win over the user's actual intent (and silently bill a foreign key). Two
# redirect/logging vars are refused from a repo .env entirely: a repo has no
# legitimate reason to point codoc's API base URL elsewhere (key exfiltration) or
# force prompt logging on — but an arbitrary/adversarial repo could. Set those in
# the shell if you genuinely need them.
_UNTRUSTED_FROM_DOTENV = frozenset({
    "CODOC_BASE_URL", "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "CODOC_LOG_PROMPTS",
})
_dotenv_path = find_dotenv(usecwd=True)
if _dotenv_path:
    for _k, _v in dotenv_values(_dotenv_path).items():
        if _v is None or _k in _UNTRUSTED_FROM_DOTENV:
            continue
        os.environ.setdefault(_k, _v)


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


class LLMConfig(BaseModel):
    provider: str  # "openai" | "anthropic" | "ollama" | "claude"
    model: str
    api_key: str | None = None
    base_url: str | None = None
    temperature: float = 0.2
    # Reasoning models spend completion budget on hidden reasoning, so the
    # budget must comfortably exceed the visible JSON we want back.
    max_tokens: int = 16000


# Per-provider default model when CODOC_MODEL is unset. The OpenAI default makes
# no sense for the Claude paths: the ``claude`` CLI takes the ``sonnet`` alias,
# while the Anthropic API needs a concrete model id.
_DEFAULT_MODELS = {
    "openai": "gpt-5.4-mini",
    "anthropic": "claude-sonnet-4-6",
    "claude": "sonnet",
}


def _is_claude_family_model(model: str) -> bool:
    """Whether a model name belongs to the Claude family (so it can run on the
    ``claude`` CLI / Anthropic API but NOT on OpenAI, and vice-versa)."""
    m = model.lower()
    return any(tag in m for tag in ("sonnet", "opus", "haiku", "claude"))


def _model_fits_provider(provider: str, model: str) -> bool:
    """Whether ``model`` can actually run on ``provider``. A cross-family mismatch
    (e.g. a globally-exported ``CODOC_MODEL=gpt-…`` leaking onto the keyless Claude
    path) is rejected so we fall back to the provider's default instead of handing
    an unusable model name to the CLI/API."""
    if provider in ("claude", "anthropic"):
        return _is_claude_family_model(model)
    if provider == "openai":
        return not _is_claude_family_model(model)
    return True  # ollama / unknown: trust the user's explicit model


def _resolve_provider() -> str:
    """Pick the LLM provider, honoring an explicit choice and otherwise inferring
    one from which key is present — keyless Claude Code is the final fallback.

    An explicit ``CODOC_PROVIDER`` always wins (the VS Code setup writes it). With
    none set we infer: an ``OPENAI_API_KEY`` means the user opted into OpenAI, an
    ``ANTHROPIC_API_KEY`` means the Anthropic API; with no key at all we default to
    ``claude`` (Claude Code, no key) so a fresh install never crashes demanding an
    OpenAI key.
    """
    explicit = os.environ.get("CODOC_PROVIDER")
    if explicit:
        return explicit
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "claude"


def get_llm_config() -> LLMConfig:
    """Build LLMConfig from environment variables.

    Provider defaults to keyless Claude Code unless a key or explicit
    ``CODOC_PROVIDER`` selects otherwise (see :func:`_resolve_provider`). The
    model and api_key both track the resolved provider.
    """
    provider = _resolve_provider()
    default_model = _DEFAULT_MODELS.get(provider, "gpt-5.4-mini")
    model = os.environ.get("CODOC_MODEL") or default_model
    if not _model_fits_provider(provider, model):
        # A stray cross-family CODOC_MODEL can't run on this provider — ignore it.
        model = default_model
    if provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    else:
        # The e2e/eval suites gate on `get_llm_config().api_key` being the OpenAI
        # key, so keep this field the OpenAI key for every non-Anthropic provider.
        api_key = os.environ.get("OPENAI_API_KEY")
    return LLMConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=os.environ.get("CODOC_BASE_URL"),
        temperature=float(os.environ.get("CODOC_TEMPERATURE", "0.2")),
        max_tokens=int(os.environ.get("CODOC_MAX_TOKENS", "16000")),
    )


def complete(prompt: str, config: LLMConfig | None = None) -> str:
    """Call the configured LLM and return the raw response string."""
    if config is None:
        config = get_llm_config()

    log_prompts = os.environ.get("CODOC_LOG_PROMPTS", "0") == "1"
    if log_prompts:
        print(f"[CODOC] PROMPT:\n{prompt}", file=sys.stderr)

    if config.provider == "openai":
        response_text = _complete_openai(prompt, config)
    elif config.provider == "ollama":
        response_text = _complete_ollama(prompt, config)
    elif config.provider == "anthropic":
        response_text = _complete_anthropic(prompt, config)
    elif config.provider in ("claude", "claude-code"):
        response_text = _complete_claude(prompt, config)
    else:
        raise ValueError(f"Unknown LLM provider: {config.provider!r}")

    if log_prompts:
        print(f"[CODOC] RESPONSE:\n{response_text}", file=sys.stderr)

    return response_text


def _complete_openai(prompt: str, config: LLMConfig) -> str:
    try:
        import openai
    except ImportError as exc:
        raise ImportError("openai package is required for provider='openai'. Run: pip install openai") from exc

    kwargs: dict = {}
    if config.api_key is not None:
        kwargs["api_key"] = config.api_key
    if config.base_url is not None:
        kwargs["base_url"] = config.base_url

    client = openai.OpenAI(**kwargs)
    # Reasoning models (GPT-5 family) spend completion budget on hidden reasoning;
    # if the visible answer gets truncated (finish_reason == "length"), retry once
    # with a larger budget so we don't return half a JSON object.
    budget = config.max_tokens
    for attempt in range(2):
        response = client.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=config.temperature,
            max_completion_tokens=budget,
        )
        choice = response.choices[0]
        content = choice.message.content or ""
        if choice.finish_reason != "length" or attempt == 1:
            return content
        budget = min(budget * 3, 64000)
    return content


def _complete_ollama(prompt: str, config: LLMConfig) -> str:
    try:
        import ollama
    except ImportError as exc:
        raise ImportError("ollama package is required for provider='ollama'. Run: pip install ollama") from exc

    response = ollama.chat(
        model=config.model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": config.temperature, "num_predict": config.max_tokens},
    )
    return response["message"]["content"]


def _complete_anthropic(prompt: str, config: LLMConfig) -> str:
    """Complete via the Anthropic API using an explicit ``ANTHROPIC_API_KEY``.

    This is the *managed-API* path (the user pasted an Anthropic key in setup), as
    opposed to the keyless ``claude`` provider that reuses a Claude Code login. The
    model must be a concrete API id (e.g. ``claude-sonnet-4-6``), not a CLI alias.
    """
    try:
        import anthropic
    except ImportError as exc:
        raise ImportError(
            "anthropic package is required for provider='anthropic'. Run: pip install anthropic"
        ) from exc

    kwargs: dict = {}
    if config.api_key is not None:
        kwargs["api_key"] = config.api_key
    if config.base_url is not None:
        kwargs["base_url"] = config.base_url

    client = anthropic.Anthropic(**kwargs)
    message = client.messages.create(
        model=config.model,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    # Concatenate the text blocks of the response (tool/thinking blocks, if any,
    # carry no .text and are skipped) — the same raw-text contract as the others.
    return "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )


def _complete_claude(prompt: str, config: LLMConfig) -> str:
    """Complete via the user's existing Claude credentials by shelling out to the
    ``claude`` CLI in headless JSON mode — no separate API key.

    Why the CLI and not the ``anthropic`` SDK: the subscription OAuth token Claude
    Code stores is *not* a general API key, so only the ``claude`` binary can reuse
    that login. Three guards make the reuse correct and cheap:

    * **never ``--bare``** — bare mode skips the OAuth/keychain read, forcing a key.
    * **drop ``ANTHROPIC_API_KEY``** from the child env — if present it silently
      overrides the subscription and bills the wrong account.
    * **neutral cwd** — run from a temp dir so codoc's *own* repo hooks / MCP /
      CLAUDE.md don't load (heavy, and risks the reflection re-triggering codoc).

    Returns the model's text (the JSON envelope's ``result``); codoc's
    ``parse_solution`` extracts the structured payload exactly as for OpenAI, so
    the prompt contract is unchanged.
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
    cmd = [
        claude, "-p",
        "--output-format", "json",
        "--max-turns", "1",
        "--allowedTools", "",  # pure completion — no tools, no agent loop
    ]
    if config.model:
        cmd += ["--model", config.model]

    # The prompt is built from repo/tree.codoc content (attacker-influenceable in a
    # shared repo). Deliver it on STDIN, never argv: a flag-shaped prompt can't be
    # re-lexed into CLI options (so `--allowedTools ""` / `--max-turns 1` hold), and
    # the prompt (source snippets) never appears in `ps`/process listings.
    proc = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True,
        cwd=tempfile.gettempdir(), env=env,
    )
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
    return str(payload.get("result", ""))


# ---------------------------------------------------------------------------
# Embedder
# ---------------------------------------------------------------------------


class EmbedderConfig(BaseModel):
    provider: str  # "openai" | "sentence-transformers"
    model: str
    api_key: str | None = None


def get_embedder_config() -> EmbedderConfig:
    """Build EmbedderConfig from environment variables."""
    return EmbedderConfig(
        provider=os.environ.get("CODOC_EMBEDDER_PROVIDER", "sentence-transformers"),
        model=os.environ.get("CODOC_EMBEDDER_MODEL", "all-MiniLM-L6-v2"),
        api_key=os.environ.get("OPENAI_API_KEY"),
    )


def embed(texts: list[str], config: EmbedderConfig | None = None) -> list[list[float]]:
    """Return embedding vectors for a list of texts."""
    if config is None:
        config = get_embedder_config()

    if config.provider == "sentence-transformers":
        return _embed_sentence_transformers(texts, config)
    elif config.provider == "openai":
        return _embed_openai(texts, config)
    else:
        raise ValueError(f"Unknown embedder provider: {config.provider!r}")


def make_embedder(config: EmbedderConfig | None = None):
    """Return a WARM embed callable ``(list[str]) -> list[list[float]]`` that loads
    its model ONCE, so repeated ``encode`` calls within a single pass don't reload
    it (unlike :func:`embed`, which rebuilds the model every call). Used by the
    opt-in semantic title dedup (D1), which embeds existing titles + candidates in
    the same pass. Raises ImportError when the provider's package is missing — the
    caller is expected to degrade gracefully (semantic dedup simply stays off)."""
    if config is None:
        config = get_embedder_config()
    if config.provider == "sentence-transformers":
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(config.model)
        return lambda texts: model.encode(list(texts)).tolist()
    if config.provider == "openai":
        return lambda texts: _embed_openai(list(texts), config)
    raise ValueError(f"Unknown embedder provider: {config.provider!r}")


def _embed_sentence_transformers(texts: list[str], config: EmbedderConfig) -> list[list[float]]:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ImportError(
            "sentence-transformers package is required for provider='sentence-transformers'. "
            "Run: pip install sentence-transformers"
        ) from exc

    model = SentenceTransformer(config.model)
    vectors = model.encode(texts)
    return vectors.tolist()


def _embed_openai(texts: list[str], config: EmbedderConfig) -> list[list[float]]:
    try:
        import openai
    except ImportError as exc:
        raise ImportError("openai package is required for provider='openai'. Run: pip install openai") from exc

    kwargs: dict = {}
    if config.api_key is not None:
        kwargs["api_key"] = config.api_key

    client = openai.OpenAI(**kwargs)
    response = client.embeddings.create(model=config.model, input=texts)
    return [item.embedding for item in sorted(response.data, key=lambda d: d.index)]
