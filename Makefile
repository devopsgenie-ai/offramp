.DEFAULT_GOAL := help
PYTHON ?= python3

.PHONY: help check rfc-validate fixture-truth spike-check

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

check: rfc-validate fixture-truth ## Run every check CI runs

rfc-validate: ## Validate RFC frontmatter
	$(PYTHON) scripts/check_rfcs.py validate

fixture-truth: ## Assert every fixture's detected facts against its truth.yaml
	$(PYTHON) scripts/check_fixtures.py truth

spike-check: ## SPIKE ONLY: reproduce the evidence in SPIKE.md
	bash scripts/spike_check.sh
