"""
CodeNav API configuration. Uses adalflow for LLM and embedder clients.
Supports OpenAI and Ollama only; config loaded from config/*.json.
"""

import os
import json
import logging
import re
from pathlib import Path
from typing import List, Union, Dict, Any

logger = logging.getLogger(__name__)

from api.openai_client import OpenAIClient
from adalflow import OllamaClient

# API keys
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if OPENAI_API_KEY:
    os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY

# Embedder: openai | ollama | huggingface
EMBEDDER_TYPE = os.environ.get("CODENAV_EMBEDDER_TYPE", "openai").lower()
if EMBEDDER_TYPE not in ("openai", "ollama", "huggingface"):
    EMBEDDER_TYPE = "openai"

# Config directory (default: server/api/config)
CONFIG_DIR = os.environ.get("CODENAV_CONFIG_DIR", None)

CLIENT_CLASSES = {
    "OpenAIClient": OpenAIClient,
    "OllamaClient": OllamaClient,
}


def _replace_env_placeholders(
    config: Union[Dict[str, Any], List[Any], str, Any],
) -> Union[Dict[str, Any], List[Any], str, Any]:
    pattern = re.compile(r"\$\{([A-Z0-9_]+)\}")

    def replacer(match: re.Match[str]) -> str:
        name = match.group(1)
        value = os.environ.get(name)
        if value is None:
            logger.warning("Env placeholder ${%s} not set, using as-is", name)
            return match.group(0)
        return value

    if isinstance(config, dict):
        return {k: _replace_env_placeholders(v) for k, v in config.items()}
    if isinstance(config, list):
        return [_replace_env_placeholders(item) for item in config]
    if isinstance(config, str):
        return pattern.sub(replacer, config)
    return config


def _load_json_config(filename: str) -> dict:
    if CONFIG_DIR:
        config_path = Path(CONFIG_DIR) / filename
    else:
        config_path = Path(__file__).parent / "config" / filename
    if not config_path.exists():
        logger.warning("Config file %s not found", config_path)
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _replace_env_placeholders(data)
    except Exception as e:
        logger.error("Error loading %s: %s", filename, e)
        return {}


def load_generator_config() -> dict:
    raw = _load_json_config("generator.json")
    if not raw or "providers" not in raw:
        return raw
    for pid, pconfig in raw["providers"].items():
        client_class = pconfig.get("client_class")
        if client_class and client_class in CLIENT_CLASSES:
            pconfig["model_client"] = CLIENT_CLASSES[client_class]
    return raw


def load_embedder_config() -> dict:
    raw = _load_json_config("embedder.json")
    if not raw:
        return raw
    for key in ("embedder", "embedder_ollama"):
        if key in raw and raw[key].get("client_class") in CLIENT_CLASSES:
            raw[key]["model_client"] = CLIENT_CLASSES[raw[key]["client_class"]]
    return raw


def get_embedder_config() -> dict:
    """Current embedder config by CODENAV_EMBEDDER_TYPE."""
    if EMBEDDER_TYPE == "ollama" and "embedder_ollama" in configs:
        return configs.get("embedder_ollama", {})
    if EMBEDDER_TYPE == "huggingface" and "embedder_huggingface" in configs:
        return configs.get("embedder_huggingface", {})
    return configs.get("embedder", {})


def get_embedder_type() -> str:
    """'ollama', 'openai', or 'huggingface'."""
    return EMBEDDER_TYPE


def get_model_config(provider: str = "openai", model: str | None = None) -> dict:
    """
    Config for LLM completion. provider in ('openai', 'ollama').
    Returns dict with model_client, model_kwargs.
    """
    if "providers" not in configs:
        raise ValueError("Generator config not loaded")
    provider_config = configs["providers"].get(provider)
    if not provider_config:
        raise ValueError(f"Provider '{provider}' not found")
    model_client = provider_config.get("model_client")
    if not model_client:
        raise ValueError(f"Model client not set for provider '{provider}'")
    if not model:
        model = provider_config.get("default_model")
    if not model:
        raise ValueError(f"No default model for provider '{provider}'")
    models = provider_config.get("models", {})
    model_params = models.get(model) or models.get(provider_config.get("default_model")) or {}
    result = {"model_client": model_client}
    if provider == "ollama":
        result["model_kwargs"] = (
            {"model": model, **model_params.get("options", {})}
            if "options" in model_params
            else {"model": model}
        )
    else:
        result["model_kwargs"] = {"model": model, **model_params}
    return result


# Default excludes for discovery (used by semantic_tree)
DEFAULT_EXCLUDED_DIRS: List[str] = [
    "./.venv/", "./venv/", "./env/", "./node_modules/", "./.git/",
    "./__pycache__/", "./.pytest_cache/", "./dist/", "./build/", "./logs/",
]
DEFAULT_EXCLUDED_FILES: List[str] = [
    ".DS_Store", ".env", ".env.*", "*.pyc", "*.min.js", "*.map",
]

configs: Dict[str, Any] = {}

_gen = load_generator_config()
_emb = load_embedder_config()

if _gen:
    configs["default_provider"] = _gen.get("default_provider", "openai")
    configs["providers"] = _gen.get("providers", {})

if _emb:
    for key in ("embedder", "embedder_ollama", "embedder_huggingface", "retriever", "text_splitter"):
        if key in _emb:
            configs[key] = _emb[key]
