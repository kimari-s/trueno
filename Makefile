# `make.local` (gitignored) overrides deployment settings so the tracked
# Makefile stays generic. Set DEPLOY_HOST / DEPLOY_PATH / PUBLIC_URL_BASE
# at minimum; SITE_TITLE is optional (defaults to "Trueno — File Host"
# so a fresh deploy already looks consistent without extra config).
-include make.local

.PHONY: help lint test fmt deploy-dry deploy clean

DEPLOY_HOST     ?= you@your.host.example.com
DEPLOY_PATH     ?= public_html/your-domain
PUBLIC_URL_BASE ?= https://a.example.test
UI_URL          ?= https://ui.example.test
SITE_TITLE      ?= 🔱Trueno
STAGE           := .deploy

help:
	@echo "Targets:"
	@echo "  lint        ruff + pyright"
	@echo "  fmt         ruff format"
	@echo "  test        pytest"
	@echo "  deploy-dry  rsync --dry-run (after substituting placeholders)"
	@echo "  deploy      rsync          (after substituting placeholders)"
	@echo
	@echo "Settings (override in make.local):"
	@echo "  DEPLOY_HOST     = $(DEPLOY_HOST)"
	@echo "  DEPLOY_PATH     = $(DEPLOY_PATH)"
	@echo "  PUBLIC_URL_BASE = $(PUBLIC_URL_BASE)"
	@echo "  UI_URL          = $(UI_URL)"
	@echo "  SITE_TITLE      = $(SITE_TITLE)"

lint:
	uv run ruff check .
	uv run pyright docroot

fmt:
	uv run ruff format .

test:
	uv run pytest

# Stage docroot/ into .deploy/ with deployment-specific placeholders
# substituted. Tracked sources keep the literal placeholder values
# (`https://a.example.test`, `Trueno — File Host`); only the staged copy
# carries the operator's overrides. Add new placeholders by extending the
# -e expression list.
stage:
	rm -rf $(STAGE)
	cp -R docroot $(STAGE)
	@for f in $(STAGE)/upload.cgi $(STAGE)/.htaccess $(STAGE)/openapi.json $(STAGE)/index.html $(STAGE)/_trueno.py $(STAGE)/info.cgi; do \
		sed -i.bak \
			-e "s|https://a\.example\.test|$(PUBLIC_URL_BASE)|g" \
			-e "s|https://ui\.example\.test|$(UI_URL)|g" \
			-e "s|🔱Trueno|$(SITE_TITLE)|g" \
			"$$f" && rm "$$f.bak"; \
	done

deploy-dry: stage
	rsync -avzn --delete --exclude 'files/' --exclude '__pycache__/' --exclude '*.pyc' $(STAGE)/ $(DEPLOY_HOST):$(DEPLOY_PATH)/

deploy: stage
	rsync -avz --delete --exclude 'files/' --exclude '__pycache__/' --exclude '*.pyc' $(STAGE)/ $(DEPLOY_HOST):$(DEPLOY_PATH)/
	ssh $(DEPLOY_HOST) "chmod 755 $(DEPLOY_PATH)/upload.cgi $(DEPLOY_PATH)/info.cgi && mkdir -p $(DEPLOY_PATH)/files && chmod 755 $(DEPLOY_PATH)/files"

clean:
	rm -rf $(STAGE)
