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
