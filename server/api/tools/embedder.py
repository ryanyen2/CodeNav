import adalflow as adal

from api.config import configs, get_embedder_type


def get_embedder(
    embedder_type: str | None = None,
) -> adal.Embedder:
    """Get embedder from config. embedder_type: 'ollama' or 'openai' (default from CODENAV_EMBEDDER_TYPE)."""
    if embedder_type is None:
        embedder_type = get_embedder_type()
    if embedder_type == "ollama":
        embedder_config = configs.get("embedder_ollama") or {}
    else:
        embedder_config = configs.get("embedder") or {}

    # --- Initialize Embedder ---
    model_client_class = embedder_config["model_client"]
    if "initialize_kwargs" in embedder_config:
        model_client = model_client_class(**embedder_config["initialize_kwargs"])
    else:
        model_client = model_client_class()
    
    # Create embedder with basic parameters
    embedder_kwargs = {"model_client": model_client, "model_kwargs": embedder_config["model_kwargs"]}
    
    embedder = adal.Embedder(**embedder_kwargs)
    
    # Set batch_size as an attribute if available (not a constructor parameter)
    if "batch_size" in embedder_config:
        embedder.batch_size = embedder_config["batch_size"]
    return embedder
