.DEFAULT_GOAL := help
PYTHON ?= python3

.PHONY: help check rfc-validate

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

check: rfc-validate ## Run every check CI runs

rfc-validate: ## Validate RFC frontmatter
	$(PYTHON) scripts/check_rfcs.py validate
