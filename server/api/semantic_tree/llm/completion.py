"""
One-shot LLM completion using api.config.get_model_config and model client.
No fallbacks: provider/model must be configured and keys set by the caller.
Set CODENAV_LOG_PROMPTS=1 to log full prompt and raw response (for debugging context/generation).
"""

import logging
import os
from typing import Optional

from adalflow.core.types import ModelType

from api.config import get_model_config

logger = logging.getLogger(__name__)
LOG_PROMPTS = os.environ.get("CODENAV_LOG_PROMPTS", "").strip() in ("1", "true", "yes")


def complete(
    prompt: str,
    provider: str,
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
) -> str:
    """
    Run one LLM completion. Uses get_model_config(provider, model); raises if not configured.
    Returns the raw response string (e.g. to parse <solution> from).
    """
    cfg = get_model_config(provider, model)
    model_client_class = cfg["model_client"]
    model_kwargs = dict(cfg["model_kwargs"])
    model_kwargs["stream"] = False

    client = model_client_class()

    # Build user input: optional system + user prompt
    if system_prompt:
        input_str = f"<START_OF_SYSTEM_PROMPT>\n{system_prompt}\n<END_OF_SYSTEM_PROMPT>\n<START_OF_USER_PROMPT>\n{prompt}\n<END_OF_USER_PROMPT>"
    else:
        input_str = prompt

    if LOG_PROMPTS:
        logger.info("[CODENAV] LLM prompt (first 2000 chars):\n%s", (input_str or "")[:2000])
        if len(input_str or "") > 2000:
            logger.info("[CODENAV] ... prompt truncated (total %d chars)", len(input_str))

    api_kwargs = client.convert_inputs_to_api_kwargs(
        input=input_str,
        model_kwargs=model_kwargs,
        model_type=ModelType.LLM,
    )

    response = client.call(api_kwargs=api_kwargs, model_type=ModelType.LLM)
    text = ""
    if hasattr(response, "raw_response") and response.raw_response is not None:
        text = response.raw_response if isinstance(response.raw_response, str) else str(response.raw_response)
    elif hasattr(response, "data") and response.data is not None:
        text = str(response.data)
    elif hasattr(response, "choices") and response.choices:
        msg = getattr(response.choices[0], "message", None)
        if msg is not None:
            content = getattr(msg, "content", None)
            if content is not None:
                text = content if isinstance(content, str) else str(content)
    if not text:
        if hasattr(response, "error") and response.error:
            raise RuntimeError(f"LLM call failed: {response.error}")
        raise RuntimeError("LLM call returned no content")
    if LOG_PROMPTS:
        logger.info("[CODENAV] LLM response (first 1500 chars):\n%s", text[:1500])
        if len(text) > 1500:
            logger.info("[CODENAV] ... response truncated (total %d chars)", len(text))
    return text
