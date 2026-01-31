#!/bin/bash
# Usage: ./scripts/setup-test-env.sh
#
# One-time setup for the test environment.
# Installs test dependencies and ensures uv is available.

set -euo pipefail

echo "Setting up test environment..."

# Find uv executable
if command -v uv &> /dev/null
then
    UV_CMD="uv"
elif [ -f ".venv/bin/uv" ]; then
    UV_CMD=".venv/bin/uv"
else
    echo "uv not found in PATH or .venv. Attempting to install to .venv/bin/uv."
    # Install uv locally if not found
    curl -LsSf https://astral.sh/uv/install.sh | sh - --target .venv/bin
    UV_CMD=".venv/bin/uv"
fi

echo "Installing test dependencies using uv..."
# Install the dev optional dependencies from pyproject.toml
"$UV_CMD" pip install -e ".[dev]"

echo "Creating necessary test directories..."
# Create data directory if it doesn't exist, as app/database.py expects it
mkdir -p data
mkdir -p tests/data # For any test-specific data needs

echo "Test environment setup complete!"
echo "You can now run tests using: make test"
echo "Or run in watch mode: make test-watch"
