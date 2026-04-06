#!/usr/bin/env bash

set -euo pipefail

# Configuration
TEST_DB="data/test_file_fridge.db"
export SECRET_KEY="dummy_key_for_e2e_testing"
export DATABASE_PATH="$TEST_DB"

echo "Setting up E2E test environment..."

# Remove old test DB if exists
if [ -f "$TEST_DB" ]; then
    echo "Removing old test database: $TEST_DB"
    rm "$TEST_DB"
fi

# Ensure data directory exists
mkdir -p data

# Initialize database
echo "Initializing database..."
uv run python -c "from app.database import init_db; init_db()"

# Create test user
echo "Creating test user..."
uv run python scripts/manage_user.py create-user admin secret123

echo "E2E environment setup complete."
