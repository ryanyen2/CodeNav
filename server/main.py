import os
import sys
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

# Configure watchfiles logger to show file paths
watchfiles_logger = logging.getLogger("watchfiles.main")
watchfiles_logger.setLevel(logging.DEBUG)  # Enable DEBUG to see file paths

# Apply watchfiles monkey patch BEFORE uvicorn import
is_development = os.environ.get("NODE_ENV") != "production"
if is_development:
    import watchfiles
    current_dir = os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(current_dir, "logs")
    
    original_watch = watchfiles.watch
    def patched_watch(*args, **kwargs):
        # Watch server dir but exclude .venv and logs so reload is stable
        watch_dirs = []
        for item in os.listdir(current_dir):
            if item in (".venv", "logs", "__pycache__", ".git"):
                continue
            item_path = os.path.join(current_dir, item)
            if os.path.isdir(item_path):
                watch_dirs.append(item_path)
            elif os.path.isfile(item_path) and item.endswith(".py"):
                watch_dirs.append(item_path)
        return original_watch(*watch_dirs, **kwargs)
    watchfiles.watch = patched_watch

import uvicorn

# Optional: warn if no API key when using OpenAI (embedder/LLM)
if not os.environ.get("OPENAI_API_KEY"):
    logger.info(
        "OPENAI_API_KEY not set. Set CODENAV_EMBEDDER_TYPE=ollama and use provider=ollama for local-only."
    )

if __name__ == "__main__":
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
