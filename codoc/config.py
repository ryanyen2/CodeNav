"""
codoc.config — LLM and embedder configuration, env-var driven.

Supports OpenAI and Ollama for completions; OpenAI and sentence-transformers for embeddings.
No adalflow dependency.

Environment variables:
    CODOC_PROVIDER          LLM provider: "openai" | "ollama"  (default "openai")
    CODOC_MODEL             Model name  (default "gpt-5.4-mini")
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
    provider: str  # "openai" | "ollama"
    model: str
    api_key: str | None = None
    base_url: str | None = None
    temperature: float = 0.2
    # Reasoning models spend completion budget on hidden reasoning, so the
    # budget must comfortably exceed the visible JSON we want back.
    max_tokens: int = 16000


def get_llm_config() -> LLMConfig:
    """Build LLMConfig from environment variables."""
    return LLMConfig(
        provider=os.environ.get("CODOC_PROVIDER", "openai"),
        model=os.environ.get("CODOC_MODEL", "gpt-5.4-mini"),
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
