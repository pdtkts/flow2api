"""Uvicorn entry: `uvicorn src.agent_gateway.main:app` or `python -m src.agent_gateway`."""
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import load_settings
from .routes_flow2api import router as flow2api_router
from .ws_agents import router as ws_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agent-gateway] %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = load_settings()
    if not s.flow2api_bearer:
        logging.getLogger(__name__).warning(
            "GATEWAY_FLOW2API_BEARER is empty — set to match Flow2API remote_browser_api_key"
        )
    if s.agent_auth_mode in {"legacy", "dual"} and not s.agent_device_token:
        logging.getLogger(__name__).warning(
            "GATEWAY_AGENT_DEVICE_TOKEN is empty — WebSocket agents cannot authenticate"
        )
    if s.agent_auth_mode in {"keygen", "dual"}:
        if s.keygen_verify_mode == "jwt" and not s.keygen_public_key:
            logging.getLogger(__name__).warning(
                "KEYGEN_PUBLIC_KEY is empty in keygen/jwt mode"
            )
        if s.keygen_verify_mode == "introspection" and not s.keygen_api_token:
            logging.getLogger(__name__).warning(
                "KEYGEN_API_TOKEN is empty in keygen/introspection mode"
            )
    yield


app = FastAPI(
    title="Flow2API Agent Gateway",
    description="Bridges Flow2API remote_browser HTTP to WebSocket agents.",
    lifespan=lifespan,
    # Operator UI: main Flow2API admin in frontend/ (Agent gateway tab), not Swagger here.
    docs_url=None,
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(flow2api_router, tags=["flow2api"])
app.include_router(ws_router, tags=["agents"])


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "flow2api-agent-gateway"}


def run() -> None:
    s = load_settings()
    uvicorn.run(
        "src.agent_gateway.main:app",
        host=s.host,
        port=s.port,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    run()
