#!/bin/bash
# Usage: ./scripts/test.sh [unit|integration|all|coverage]
#
# Runs pytest with various configurations.
#
# Arguments:
#   unit:      Runs only tests marked with @pytest.mark.unit.
#   integration: Runs only tests marked with @pytest.mark.integration.
#   all:       Runs all tests (default if no argument or invalid argument).
#   coverage:  Runs all tests and generates an HTML coverage report.
#
# Examples:
#   ./scripts/test.sh unit
#   ./scripts/test.sh coverage

set -euo pipefail

# Find uv executable
if command -v uv &> /dev/null
then
    UV_CMD="uv"
elif [ -f ".venv/bin/uv" ]; then
    UV_CMD=".venv/bin/uv"
else
    echo "Error: 'uv' not found. Please install uv or activate your virtual environment."
    echo "You can install uv using: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

case "${1:-all}" in
  unit)
    echo "Running unit tests..."
    "$UV_CMD" run pytest -m "not integration"
    ;;
  integration)
    echo "Running integration tests..."
    "$UV_CMD" run pytest -m integration
    ;;
  coverage)
    echo "Running all tests with coverage report..."
    "$UV_CMD" run pytest --cov=app --cov-report=html --cov-fail-under=80
    echo "Coverage report generated at htmlcov/index.html"
    ;;
  *)
    echo "Running all tests..."
    "$UV_CMD" run pytest
    ;;
esac
