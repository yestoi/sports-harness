# Sportsbook harness — deploy targets (mirrors ~/dev/nas-media-stack conventions)
.PHONY: deploy-nas init-nas ssh-nas logs-nas status-nas tunnel-nas stop-mac test help

NAS_IP    ?= $(shell grep '^NAS_IP=' .env.nas 2>/dev/null | cut -d= -f2)
NAS_USER  ?= $(shell grep '^NAS_USER=' .env.nas 2>/dev/null | cut -d= -f2)
NAS_STACK ?= $(shell grep '^NAS_STACK_DIR=' .env.nas 2>/dev/null | cut -d= -f2)
SERVE_PORT ?= $(shell grep '^SERVE_PORT=' deploy/nas.env 2>/dev/null | cut -d= -f2)
GREEN := \033[0;32m
NC    := \033[0m

deploy-nas: ## Push source, compose env, and secrets to the NAS; build; migrate; seed; start
	@printf "$(GREEN)[DEPLOY]$(NC) Pushing to $(NAS_USER)@$(NAS_IP):$(NAS_STACK)\n"
	@ssh $(NAS_USER)@$(NAS_IP) 'mkdir -p $(NAS_STACK)/secrets $(NAS_STACK)/pgdata'
	@tar cf - --exclude='__pycache__' pyproject.toml Dockerfile .dockerignore docker-compose.yml harness docs/runbooks \
		| ssh $(NAS_USER)@$(NAS_IP) 'tar xf - -C $(NAS_STACK)'
	@scp -O deploy/nas.env $(NAS_USER)@$(NAS_IP):$(NAS_STACK)/.env
	@scp -O secrets/odds_api_key secrets/kalshi_key_id secrets/kalshi_private_key.pem $(NAS_USER)@$(NAS_IP):$(NAS_STACK)/secrets/
	@ssh $(NAS_USER)@$(NAS_IP) 'test -f $(NAS_STACK)/secrets/dashboard_token || openssl rand -hex 32 > $(NAS_STACK)/secrets/dashboard_token'
	@ssh $(NAS_USER)@$(NAS_IP) 'chmod 600 $(NAS_STACK)/secrets/*'
	@printf "$(GREEN)[DEPLOY]$(NC) Building image and starting postgres...\n"
	@ssh $(NAS_USER)@$(NAS_IP) 'cd $(NAS_STACK) && docker compose build && docker compose up -d postgres'
	@printf "$(GREEN)[DEPLOY]$(NC) Schema + teams (idempotent; required after every upgrade)...\n"
	@ssh $(NAS_USER)@$(NAS_IP) 'cd $(NAS_STACK) && docker compose run --rm app-run init-db && docker compose run --rm app-run seed-teams && docker compose run --rm app-run variants register'
	@printf "$(GREEN)[DEPLOY]$(NC) Starting services...\n"
	@ssh $(NAS_USER)@$(NAS_IP) 'cd $(NAS_STACK) && docker compose up -d'
	@printf "$(GREEN)[DEPLOY]$(NC) Done. Run: make status-nas\n"

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

test: ## Run the test suite (needs DATABASE_URL_TEST)
	@.venv/bin/pytest -q

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'
