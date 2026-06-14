"""
codoc.config — LLM and embedder configuration, env-var driven.

Supports OpenAI, Ollama, and Claude for completions; OpenAI and
sentence-transformers for embeddings. No adalflow dependency.

The ``claude`` provider reuses the user's *existing* Claude credentials by
shelling out to the ``claude`` CLI in headless JSON mode — no separate API key.
This is what lets the VS Code extension offer a single-sign-in setup: codoc's
own reflection calls ride on the same auth Claude Code already resolved.

Environment variables:
    CODOC_PROVIDER          LLM provider: "openai" | "ollama" | "claude"  (default "openai")
    CODOC_MODEL             Model name  (default "gpt-5.4-mini"; "sonnet" when provider="claude")
    OPENAI_API_KEY          API key for OpenAI (required when provider=openai)
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
from dotenv import load_dotenv

# override=True so the project's .env is authoritative over stale shell exports
# (e.g. a globally-exported CODOC_MAX_TOKENS that would otherwise win).
load_dotenv(override=True)


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


class LLMConfig(BaseModel):
    provider: str  # "openai" | "ollama" | "claude"
    model: str
    api_key: str | None = None
    base_url: str | None = None
    temperature: float = 0.2
    # Reasoning models spend completion budget on hidden reasoning, so the
    # budget must comfortably exceed the visible JSON we want back.
    max_tokens: int = 16000


def get_llm_config() -> LLMConfig:
    """Build LLMConfig from environment variables.

    When ``CODOC_MODEL`` is unset the default tracks the provider: the OpenAI
    default (``gpt-5.4-mini``) makes no sense for Claude, so ``claude`` falls
    back to the ``sonnet`` alias the ``claude`` CLI understands.
    """
    provider = os.environ.get("CODOC_PROVIDER", "openai")
    model = os.environ.get("CODOC_MODEL")
    if model is None:
        model = "sonnet" if provider in ("claude", "anthropic") else "gpt-5.4-mini"
    return LLMConfig(
        provider=provider,
        model=model,
        api_key=os.environ.get("OPENAI_API_KEY"),
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
    elif config.provider in ("claude", "anthropic"):
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
