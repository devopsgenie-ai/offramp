.DEFAULT_GOAL := help
PYTHON ?= python3

.PHONY: help check rfc-validate test

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

check: rfc-validate test ## Run every check CI runs

rfc-validate: ## Validate RFC frontmatter
	$(PYTHON) scripts/check_rfcs.py validate

test: ## Run the unit and golden tests
	$(PYTHON) -m pytest -q
