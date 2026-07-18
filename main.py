"""Flow2API - Main Entry Point"""
import copy

from src.main import app
from src.core.logger import SensitiveAccessLogFilter
import uvicorn

if __name__ == "__main__":
    from src.core.config import config

    log_config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    log_config.setdefault("filters", {})["sensitive_query"] = {
        "()": SensitiveAccessLogFilter,
    }
    access_handler = log_config.get("handlers", {}).get("access", {})
    access_filters = list(access_handler.get("filters", []))
    access_filters.append("sensitive_query")
    access_handler["filters"] = access_filters

    uvicorn.run(
        "src.main:app",
        host=config.server_host,
        port=config.server_port,
        reload=False,
        log_config=log_config,
    )
