"""
One-shot LLM completion using api.config.get_model_config and model client.
No fallbacks: provider/model must be configured and keys set by the caller.
"""

import logging
from typing import Optional

from adalflow.core.types import ModelType

from api.config import get_model_config

logger = logging.getLogger(__name__)


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

    api_kwargs = client.convert_inputs_to_api_kwargs(
        input=input_str,
        model_kwargs=model_kwargs,
        model_type=ModelType.LLM,
    )

    response = client.call(api_kwargs=api_kwargs, model_type=ModelType.LLM)
    if hasattr(response, "raw_response") and response.raw_response is not None:
        return response.raw_response
    if hasattr(response, "data") and response.data is not None:
        return str(response.data)
    if hasattr(response, "error") and response.error:
        raise RuntimeError(f"LLM call failed: {response.error}")
    raise RuntimeError("LLM call returned no content")
