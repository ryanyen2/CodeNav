import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load .env from server directory (so OPENAI_API_KEY, CODENAV_EMBEDDER_TYPE, etc. are set)
_server_dir = Path(__file__).resolve().parent
load_dotenv(_server_dir / ".env")

from api.logging_config import setup_logging

# Configure logging
setup_logging()
logger = logging.getLogger(__name__)

# --- Bidirectional sync policy (env-driven, validated on boot) ---
UNDERSPEC_VALID = ("status-or-missing-anchor",)
FORWARD_CONFLICT_VALID = ("code_wins_grounded_user_wins_underspec",)
INVERSE_SCOPE_VALID = ("best_effort",)


def _load_sync_policy():
    """Load and validate bidirectional sync policy from environment. Log diagnostics."""
    underspec = os.environ.get("CODENAV_UNDERSPEC_MODE", "status-or-missing-anchor").strip()
    forward = os.environ.get("CODENAV_FORWARD_CONFLICT_RULE", "code_wins_grounded_user_wins_underspec").strip()
    inverse = os.environ.get("CODENAV_INVERSE_SCOPE", "best_effort").strip()
    if underspec not in UNDERSPEC_VALID:
        underspec = UNDERSPEC_VALID[0]
        logger.warning("CODENAV_UNDERSPEC_MODE invalid; using %s", underspec)
    if forward not in FORWARD_CONFLICT_VALID:
        forward = FORWARD_CONFLICT_VALID[0]
        logger.warning("CODENAV_FORWARD_CONFLICT_RULE invalid; using %s", forward)
    if inverse not in INVERSE_SCOPE_VALID:
        inverse = INVERSE_SCOPE_VALID[0]
        logger.warning("CODENAV_INVERSE_SCOPE invalid; using %s", inverse)
    return {
        "underspec_definition_mode": underspec,
        "forward_conflict_rule": forward,
        "inverse_generation_scope": inverse,
    }


def _log_sync_policy_diagnostics(policy: dict) -> None:
    """Emit one-line startup summary of active sync policy."""
    logger.info(
        "[CODENAV] sync policy: underspec=%s forward=%s inverse=%s",
        policy["underspec_definition_mode"],
        policy["forward_conflict_rule"],
        policy["inverse_generation_scope"],
    )


# Configure watchfiles logger to show file paths
watchfiles_logger = logging.getLogger("watchfiles.main")
watchfiles_logger.setLevel(logging.DEBUG)  # Enable DEBUG to see file paths

# Apply watchfiles monkey patch BEFORE uvicorn import
is_development = os.environ.get("NODE_ENV") != "production"
if is_development:
    import watchfiles

    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Repo root: server is server/, so parent of server is repo root
    repo_root = Path(current_dir).resolve().parent
    src_dir = repo_root / "src"

    original_watch = watchfiles.watch

    def patched_watch(*args, **kwargs):
        # Watch server dir + repo src/ so TS sync/merge/underspec changes trigger reload
        watch_dirs = []
        for item in os.listdir(current_dir):
            if item in (".venv", "logs", "__pycache__", ".git"):
                continue
            item_path = os.path.join(current_dir, item)
            if os.path.isdir(item_path):
                watch_dirs.append(item_path)
            elif os.path.isfile(item_path) and item.endswith(".py"):
                watch_dirs.append(item_path)
        if src_dir.is_dir():
            watch_dirs.append(str(src_dir))
        return original_watch(*watch_dirs, **kwargs)

    watchfiles.watch = patched_watch

import uvicorn

# Optional: warn if no API key when using OpenAI (LLM)
if not os.environ.get("OPENAI_API_KEY"):
    logger.info(
        "OPENAI_API_KEY not set. Use provider=ollama for local-only LLM."
    )

if __name__ == "__main__":
    # Load and log sync policy on startup
    sync_policy = _load_sync_policy()
    _log_sync_policy_diagnostics(sync_policy)

    # Get port from environment variable or use default
    port = int(os.environ.get("PORT", 8001))

    # Import the app here to ensure environment variables are set first
    from api.api import app

    logger.info("Starting CodeNav API on port %s", port)

    # Run the FastAPI app with uvicorn
    uvicorn.run(
        "api.api:app",
        host="0.0.0.0",
        port=port,
        reload=is_development,
        reload_excludes=["**/logs/*", "**/__pycache__/*", "**/*.pyc"] if is_development else None,
    )
