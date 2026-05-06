"""
Wraps sentence-transformers to match the adalflow Embedder call interface
(out.data[0].embedding). No HuggingFace token needed when the model is
already in the local HF cache.
"""

from dataclasses import dataclass
from typing import List


@dataclass
class _EmbedData:
    embedding: object  # numpy array or list


@dataclass
class _EmbedOutput:
    data: List[_EmbedData]


class HuggingFaceEmbedder:
    """
    Drop-in replacement for adal.Embedder for local sentence-transformer models.
    Usage: embedder = HuggingFaceEmbedder(model_name="google/embeddinggemma-300M")
    """

    def __init__(
        self,
        model_name: str = "google/embeddinggemma-300M",
        prompt_name: str | None = None,
    ) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self._prompt_name = prompt_name

    def __call__(self, input: str, **_kwargs) -> _EmbedOutput:
        kwargs: dict = {}
        if self._prompt_name:
            kwargs["prompt_name"] = self._prompt_name
        vec = self._model.encode(input, **kwargs)
        return _EmbedOutput(data=[_EmbedData(embedding=vec)])
