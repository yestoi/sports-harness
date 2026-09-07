# Sportsbook harness — deploy targets (mirrors ~/dev/nas-media-stack conventions)
.PHONY: deploy-nas deploy-nas-app init-nas ssh-nas logs-nas status-nas tunnel-nas stop-mac test preflight verify-summary worktree worktree-rm help

NAS_IP    ?= $(shell grep '^NAS_IP=' .env.nas 2>/dev/null | cut -d= -f2)
NAS_USER  ?= $(shell grep '^NAS_USER=' .env.nas 2>/dev/null | cut -d= -f2)
NAS_STACK ?= $(shell grep '^NAS_STACK_DIR=' .env.nas 2>/dev/null | cut -d= -f2)
SERVE_PORT ?= $(shell grep '^SERVE_PORT=' deploy/nas.env 2>/dev/null | cut -d= -f2)
GREEN := \033[0;32m
NC    := \033[0m

# Computed once per make invocation (parse time), same convention as NAS_IP etc. above.
BUILD_SHA  := $(shell git rev-parse --short HEAD)$(if $(shell git status --porcelain),-dirty,)
BUILD_TIME := $(shell date -u +%FT%TZ)

deploy-nas: ## Push source, compose env, and secrets to the NAS; build; migrate; seed; start
	@if [ -n "$$(git status --porcelain)" ] && [ "$$ALLOW_DIRTY" != "1" ]; then \
		echo "refusing to deploy a dirty tree; commit first (or ALLOW_DIRTY=1)"; exit 1; fi
	@printf "$(GREEN)[DEPLOY]$(NC) Pushing to $(NAS_USER)@$(NAS_IP):$(NAS_STACK)\n"
	@ssh $(NAS_USER)@$(NAS_IP) 'mkdir -p $(NAS_STACK)/secrets $(NAS_STACK)/pgdata'
	@tar cf - --exclude='__pycache__' pyproject.toml constraints.txt Dockerfile .dockerignore docker-compose.yml harness docs/runbooks \
		| ssh $(NAS_USER)@$(NAS_IP) 'tar xf - -C $(NAS_STACK)'
	@mkdir -p build
	@cp deploy/nas.env build/nas.env
	@printf 'BUILD_SHA=%s\nBUILD_TIME=%s\n' "$(BUILD_SHA)" "$(BUILD_TIME)" >> build/nas.env
	@printf "$(GREEN)[DEPLOY]$(NC) build $(BUILD_SHA) $(BUILD_TIME)\n"
	@scp -O build/nas.env $(NAS_USER)@$(NAS_IP):$(NAS_STACK)/.env
	@scp -O secrets/odds_api_key secrets/kalshi_key_id secrets/kalshi_private_key.pem $(NAS_USER)@$(NAS_IP):$(NAS_STACK)/secrets/
# Optional secrets: absent ones are skipped instead of failing the deploy.
# secrets/backup_age_key is the age *private* key and is never pushed to the NAS; only
# deploy/backup_age.pub goes there, so a NAS compromise cannot decrypt the backups.
	@for f in kalshi_demo_key_id kalshi_demo_private_key.pem anthropic_api_key; do \
		if [ -f secrets/$$f ]; then scp -O secrets/$$f $(NAS_USER)@$(NAS_IP):$(NAS_STACK)/secrets/; fi; \
	done
	@ssh $(NAS_USER)@$(NAS_IP) 'test -f $(NAS_STACK)/secrets/dashboard_token || openssl rand -hex 32 > $(NAS_STACK)/secrets/dashboard_token'
	@ssh $(NAS_USER)@$(NAS_IP) 'chmod 600 $(NAS_STACK)/secrets/*'
	@printf "$(GREEN)[DEPLOY]$(NC) Building image and starting postgres...\n"
	@ssh $(NAS_USER)@$(NAS_IP) 'cd $(NAS_STACK) && docker compose build && docker compose up -d postgres'
	@printf "$(GREEN)[DEPLOY]$(NC) Schema + teams (idempotent; required after every upgrade)...\n"
	@ssh $(NAS_USER)@$(NAS_IP) 'cd $(NAS_STACK) && docker compose run --rm app-run init-db && { docker compose run --rm app-run seed-teams || echo "[DEPLOY] WARNING: seed-teams failed; teams unchanged"; } && docker compose run --rm app-run variants register'
	@printf "$(GREEN)[DEPLOY]$(NC) Starting services...\n"
	@ssh $(NAS_USER)@$(NAS_IP) 'cd $(NAS_STACK) && docker compose up -d'
	@printf "$(GREEN)[DEPLOY]$(NC) Done. Run: make status-nas\n"

deploy-nas-app: ## Same push, but restart only app-run/app-serve/app-exec (app-ws keeps its socket)
	@if [ -n "$$(git status --porcelain)" ] && [ "$$ALLOW_DIRTY" != "1" ]; then \
		echo "refusing to deploy a dirty tree; commit first (or ALLOW_DIRTY=1)"; exit 1; fi
	@docker compose config --services >/dev/null 2>&1 || { \
		echo "deploy-nas-app: 'docker compose config' failed here, so app-exec cannot be verified"; exit 1; }
	@docker compose config --services | grep -qx app-exec || { \
		echo "deploy-nas-app: no app-exec service in docker-compose.yml (it arrives with phase 3); use make deploy-nas"; exit 1; }
	@printf "$(GREEN)[DEPLOY]$(NC) Pushing to $(NAS_USER)@$(NAS_IP):$(NAS_STACK) (app only)\n"
	@ssh $(NAS_USER)@$(NAS_IP) 'mkdir -p $(NAS_STACK)/secrets $(NAS_STACK)/pgdata'
	@tar cf - --exclude='__pycache__' pyproject.toml constraints.txt Dockerfile .dockerignore docker-compose.yml harness docs/runbooks \
		| ssh $(NAS_USER)@$(NAS_IP) 'tar xf - -C $(NAS_STACK)'
	@mkdir -p build
	@cp deploy/nas.env build/nas.env
	@printf 'BUILD_SHA=%s\nBUILD_TIME=%s\n' "$(BUILD_SHA)" "$(BUILD_TIME)" >> build/nas.env
	@printf "$(GREEN)[DEPLOY]$(NC) build $(BUILD_SHA) $(BUILD_TIME)\n"
	@scp -O build/nas.env $(NAS_USER)@$(NAS_IP):$(NAS_STACK)/.env
	@scp -O secrets/odds_api_key secrets/kalshi_key_id secrets/kalshi_private_key.pem $(NAS_USER)@$(NAS_IP):$(NAS_STACK)/secrets/
	@for f in kalshi_demo_key_id kalshi_demo_private_key.pem anthropic_api_key; do \
		if [ -f secrets/$$f ]; then scp -O secrets/$$f $(NAS_USER)@$(NAS_IP):$(NAS_STACK)/secrets/; fi; \
	done
	@ssh $(NAS_USER)@$(NAS_IP) 'test -f $(NAS_STACK)/secrets/dashboard_token || openssl rand -hex 32 > $(NAS_STACK)/secrets/dashboard_token'
	@ssh $(NAS_USER)@$(NAS_IP) 'chmod 600 $(NAS_STACK)/secrets/*'
# The image is built before init-db so the schema step runs the pushed code, exactly as in
# deploy-nas; the build in the final command is then a cache hit.
	@printf "$(GREEN)[DEPLOY]$(NC) Building app image...\n"
	@ssh $(NAS_USER)@$(NAS_IP) 'cd $(NAS_STACK) && docker compose build app-run app-serve app-exec'
	@printf "$(GREEN)[DEPLOY]$(NC) Schema + teams (idempotent; required after every upgrade)...\n"
	@ssh $(NAS_USER)@$(NAS_IP) 'cd $(NAS_STACK) && docker compose run --rm app-run init-db && { docker compose run --rm app-run seed-teams || echo "[DEPLOY] WARNING: seed-teams failed; teams unchanged"; } && docker compose run --rm app-run variants register'
	@printf "$(GREEN)[DEPLOY]$(NC) Rebuilding and restarting app-run, app-serve, app-exec only...\n"
	@ssh $(NAS_USER)@$(NAS_IP) 'cd $(NAS_STACK) && docker compose build app-run app-serve app-exec && docker compose up -d --no-deps app-run app-serve app-exec'
	@printf "$(GREEN)[DEPLOY]$(NC) Done (app-ws untouched). Run: make status-nas\n"

status-nas: ## Container status and /healthz on the NAS
	@ssh $(NAS_USER)@$(NAS_IP) 'cd $(NAS_STACK) && docker compose ps --format "table {{.Name}}\t{{.Status}}"; echo; curl -s -w " [%{http_code}]\n" http://127.0.0.1:$(SERVE_PORT)/healthz'

logs-nas: ## Stream logs from the NAS stack
	@ssh $(NAS_USER)@$(NAS_IP) 'cd $(NAS_STACK) && docker compose logs -f --tail 50'

ssh-nas: ## SSH to the NAS in the stack directory
	@ssh -t $(NAS_USER)@$(NAS_IP) 'cd $(NAS_STACK) && exec $$SHELL -l'

tunnel-nas: ## Forward the NAS health endpoint to http://localhost:$(SERVE_PORT)/healthz
	@printf "  healthz: http://localhost:$(SERVE_PORT)/healthz\n"
	@ssh -N -L $(SERVE_PORT):127.0.0.1:$(SERVE_PORT) $(NAS_USER)@$(NAS_IP)

stop-mac: ## Stop the stopgap recorder on this Mac (avoid double credit spend once the NAS is live)
	@docker compose down

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'

# ---- autopilot helpers (added 2026-09-07) ----
# The test DB is per branch so implementers in worktrees never share a schema; scripts/testdb.py creates it.
# PYTHONPATH=. makes pytest import the checkout it runs in (the editable install would otherwise resolve
# `harness` to the main checkout from inside a worktree).
VENV    ?= $(if $(wildcard .venv/bin/pytest),.venv,$(HOME)/dev/sports/.venv)
TEST_DB ?= harness_test_$(shell git branch --show-current | tr -c 'a-z0-9\n' '_' | tr -d '\n')

test: ## Full suite against a per-branch test DB on localhost:5433 (created if missing)
	@URL=$$($(VENV)/bin/python scripts/testdb.py $(TEST_DB)) && DATABASE_URL_TEST=$$URL PYTHONPATH=. $(VENV)/bin/pytest -q

preflight: ## Session preflight: clock, git, Mac, test DB, secrets, posture, tunnel, NAS, stamp, game window
	@scripts/preflight.sh

verify-summary: ## Deterministic Layer 3: /api/summary versus SQL (DEPLOY_SHA=<sha>)
	@$(VENV)/bin/python scripts/verify_summary.py $(DEPLOY_SHA)

worktree: ## Implementer worktree: make worktree BR=<branch> [BASE=main]; prints the path
	@scripts/worktree.sh add $(BR) $(or $(BASE),main)

worktree-rm: ## Remove an implementer worktree: make worktree-rm BR=<branch>
	@scripts/worktree.sh rm $(BR)
