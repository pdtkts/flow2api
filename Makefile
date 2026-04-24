# Default stack: Flow2API + agent-gateway + redis (see docker-compose.agent-gateway.yml)
COMPOSE_DEFAULT = docker compose -f docker-compose.yml -f docker-compose.agent-gateway.yml
# Build flow2api + agent-gateway from local git (fresh UI + gateway code; tags flow2api:local, flow2api-agent-gateway:local)
COMPOSE_LOCAL = $(COMPOSE_DEFAULT) -f docker-compose.local-build.yml

# Clear terminal (Unix), pull latest, rebuild images, start detached
.PHONY: pull-up-docker
pull-up-docker:
	@command -v clear >/dev/null 2>&1 && clear || true; git pull && $(COMPOSE_DEFAULT) up -d --build

# Same without clearing the screen (portable; use from CI or if `clear` is unwanted)
.PHONY: pull-build-up
pull-build-up:
	git pull && $(COMPOSE_DEFAULT) up -d --build

# pull + build both from repo (not only ghcr image)
.PHONY: pull-build-local
pull-build-local:
	git pull && $(COMPOSE_LOCAL) up -d --build

# Flow2API + agent-gateway + redis + cloudflared (TUNNEL_TOKEN in .env). Add Public Hostname → http://agent-gateway:9080
COMPOSE_AGENT_TUNNEL = docker compose -f docker-compose.yml -f docker-compose.agent-gateway.yml -f docker-compose.tunnel.yml -f docker-compose.agent-gateway.tunnel.yml

.PHONY: pull-up-agent-tunnel
pull-up-agent-tunnel:
	@command -v clear >/dev/null 2>&1 && clear || true; git pull && $(COMPOSE_AGENT_TUNNEL) up -d --build

# Same as pull-up-agent-tunnel but flow2api + agent-gateway are built from local git (see docker-compose.local-build.yml)
COMPOSE_AGENT_TUNNEL_LOCAL = $(COMPOSE_AGENT_TUNNEL) -f docker-compose.local-build.yml

.PHONY: pull-up-agent-tunnel-local
pull-up-agent-tunnel-local:
	@command -v clear >/dev/null 2>&1 && clear || true; git pull && $(COMPOSE_AGENT_TUNNEL_LOCAL) up -d --build

# Headed (Docker) — rebuild frontend + image, then start detached
COMPOSE_HEADED = docker compose -f docker-compose.headed.yml
COMPOSE_HEADED_TUNNEL = docker compose -f docker-compose.headed.yml -f docker-compose.headed.tunnel.yml

.PHONY: headed
headed:
	$(COMPOSE_HEADED) build --no-cache && $(COMPOSE_HEADED) up -d

# git pull + rebuild + headed stack with Cloudflare Tunnel (see .env for TUNNEL_TOKEN, FLOW2API_API_ONLY_HOST)
.PHONY: headed-tunnel-pull
headed-tunnel-pull:
	git pull && $(COMPOSE_HEADED_TUNNEL) up -d --build

.PHONY: headed-up
headed-up:
	$(COMPOSE_HEADED) up -d

.PHONY: headed-logs
headed-logs:
	$(COMPOSE_HEADED) logs -f
