# Update Guide

This document covers the procedures for updating File Fridge and explains our versioning strategy.

## Versioning Strategy

File Fridge follows [Semantic Versioning (SemVer)](https://semver.org/):

- **Major** (x.0.0): Significant changes that may include breaking API changes or major architectural overhauls.
- **Minor** (0.x.0): New features and improvements, maintained in a backward-compatible manner.
- **Patch** (0.0.x): Backward-compatible bug fixes and security updates.

> **Note**: File Fridge is currently under active development. While we strive for stability, updates during the 0.x.x phase may occasionally introduce breaking changes. Always check the release notes and back up your database before updating.

## Updating Docker Installations (Recommended)

If you are using the official Docker image:

### 1. Pull the Latest Image

```bash
docker-compose pull
```

### 2. Restart the Container

```bash
docker-compose up -d
```

Docker Compose will detect the new image and recreate the container while preserving your data volumes.

### 3. Database Migrations

Database migrations are handled automatically on startup. The application uses Alembic to ensure your database schema stays up to date with the application code.

## Updating Manual Installations

### 1. Pull Latest Changes

```bash
git pull origin main
```

### 2. Update Dependencies

**Using uv:**
```bash
uv sync
```

**Using pip:**
```bash
pip install -r requirements.txt
```

### 3. Run Migrations

```bash
uv run alembic upgrade head
```

### 4. Restart the Service

Restart your `uvicorn` process or systemd service.

## Backing Up

Before performing any update, it is highly recommended to back up your database:

1. Locate your database file (default is `data/file_fridge.db`).
2. Create a copy of the file:
   ```bash
   cp data/file_fridge.db data/file_fridge.db.bak.$(date +%Y%m%d)
   ```

In case of an update failure, you can restore this file to return to your previous state.

## Troubleshooting Schema Inconsistencies

If you encounter database errors after updating (e.g., "no such column: filter_level"), your database may have schema inconsistencies from an earlier version.

**Symptoms:**
- 500 Internal Server Error on API endpoints
- Error messages about missing columns
- `alembic upgrade head` fails

**Solution:**

Run the schema fix script to reconcile your database with the current schema:

```bash
uv run python scripts/fix_database_schema.py
```

This script will:
1. Fix the Alembic version tracking
2. Add any missing database columns
3. Remove legacy columns that are no longer needed

After running the script, continue with the normal update process:

```bash
uv run alembic upgrade head
```

**Docker Users:**

If you're using Docker, you may need to run the fix script from within the container:

```bash
docker-compose exec app python scripts/fix_database_schema.py
docker-compose restart app
```
