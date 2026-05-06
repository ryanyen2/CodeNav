import adalflow as adal

from api.config import configs, get_embedder_type


def get_embedder(
    embedder_type: str | None = None,
):
    """
    Get embedder from config. embedder_type: 'ollama', 'openai', or 'huggingface'
    (default from CODENAV_EMBEDDER_TYPE). Returns adal.Embedder for openai/ollama,
    HuggingFaceEmbedder for huggingface (same call interface: out.data[0].embedding).
    """
    if embedder_type is None:
        embedder_type = get_embedder_type()

    if embedder_type == "huggingface":
        from api.huggingface_client import HuggingFaceEmbedder
        hf_cfg = configs.get("embedder_huggingface", {})
        init_kwargs = hf_cfg.get("initialize_kwargs", {})
        return HuggingFaceEmbedder(**init_kwargs)

    if embedder_type == "ollama":
        embedder_config = configs.get("embedder_ollama") or {}
    else:
        embedder_config = configs.get("embedder") or {}

    model_client_class = embedder_config["model_client"]
    if "initialize_kwargs" in embedder_config:
        model_client = model_client_class(**embedder_config["initialize_kwargs"])
    else:
        model_client = model_client_class()

    embedder_kwargs = {"model_client": model_client, "model_kwargs": embedder_config["model_kwargs"]}
    embedder = adal.Embedder(**embedder_kwargs)

    if "batch_size" in embedder_config:
        embedder.batch_size = embedder_config["batch_size"]
    return embedder
