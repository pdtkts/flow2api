"""Extension-first generation execution helper."""

from typing import Any, Awaitable, Callable

from ..core.logger import debug_logger


class ExtensionGenerationService:
    """Small adapter for extension-first execution.

    The current implementation delegates execution to the provided callable and
    keeps all route/fallback decisions in GenerationHandler.
    """

    async def execute(self, operation_name: str, operation: Callable[[], Awaitable[Any]]) -> Any:
        debug_logger.log_info(f"[EXT-GEN] execute via extension path: op={operation_name}")
        return await operation()
