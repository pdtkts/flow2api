"""Flow2API - Main Entry Point"""
import copy

from src.main import app
from src.core.logger import SensitiveAccessLogFilter
import uvicorn


def build_uvicorn_log_config():
    """Build Uvicorn logging config with secret redaction on HTTP and WebSocket logs."""
    log_config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    filter_name = "sensitive_query"
    log_config.setdefault("filters", {})[filter_name] = {
        "()": SensitiveAccessLogFilter,
    }

    for handler_name in ("access", "default"):
        handler = log_config.setdefault("handlers", {}).setdefault(handler_name, {})
        filters = list(handler.get("filters", []))
        if filter_name not in filters:
            filters.append(filter_name)
        handler["filters"] = filters

    return log_config


if __name__ == "__main__":
    from src.core.config import config

    uvicorn.run(
        "src.main:app",
        host=config.server_host,
        port=config.server_port,
        reload=False,
        log_config=build_uvicorn_log_config(),
    )
