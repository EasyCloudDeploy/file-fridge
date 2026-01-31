.PHONY: test test-unit test-coverage test-watch setup-test-env

test: ## Run all tests
	./scripts/test.sh all

test-unit: ## Run unit tests only
	./scripts/test.sh unit

test-integration: ## Run integration tests only
	./scripts/test.sh integration

test-coverage: ## Run tests with coverage report
	./scripts/test.sh coverage

test-watch: ## Run tests in watch mode
	./scripts/test-watch.sh

setup-test-env: ## One-time test environment setup
	./scripts/setup-test-env.sh

help: ## Display this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%%-20s\033[0m %s\n", $$1, $$2}'
