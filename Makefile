# Headed (Docker) — rebuild frontend + image, then start detached
COMPOSE_HEADED = docker compose -f docker-compose.headed.yml

.PHONY: headed
headed:
	$(COMPOSE_HEADED) build --no-cache && $(COMPOSE_HEADED) up -d

.PHONY: headed-up
headed-up:
	$(COMPOSE_HEADED) up -d

.PHONY: headed-logs
headed-logs:
	$(COMPOSE_HEADED) logs -f
