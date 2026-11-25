"""Run the local interface with ``python -m wikimind.webapp``."""

from __future__ import annotations

import uvicorn

from ..llm.secrets import load_dotenv
from .config import WebConfig


def main() -> None:
    """Bind the dev server to loopback by default; there is no authentication."""

    load_dotenv()
    config = WebConfig.from_env()
    config.upload_dir.mkdir(parents=True, exist_ok=True)
    config.costs_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"WikiMind local interface: http://{config.host}:{config.port} (no auth)")
    uvicorn.run(
        "wikimind.webapp.app:app",
        host=config.host,
        port=config.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
