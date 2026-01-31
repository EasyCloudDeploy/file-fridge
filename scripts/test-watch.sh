#!/bin/bash
# Usage: ./scripts/test-watch.sh
#
# Runs pytest in watch mode, re-running tests on file changes.
# Requires pytest-watch (ptw) to be installed.

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

echo "Running tests in watch mode (requires pytest-watch)..."
echo "Press Ctrl+C to stop."

# Check if pytest-watch is installed in the current environment
if ! "$UV_CMD" run ptw --version &> /dev/null; then
    echo "pytest-watch (ptw) not found. Installing..."
    "$UV_CMD" pip install pytest-watch
fi

"$UV_CMD" run ptw -- --cov=app --cov-report=term-missing
