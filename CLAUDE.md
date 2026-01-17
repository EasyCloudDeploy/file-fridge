# CLAUDE.md - AI Assistant Guide for File Fridge

## Quick Reference

**Project:** File Fridge - Full-stack Python application for managing file cold storage
**Version:** 0.0.22 (Beta)
**License:** Apache License 2.0
**Tech Stack:** FastAPI + SQLAlchemy + SQLite + Bootstrap 5 + Vanilla JS
**Python:** 3.8 - 3.12
**API Endpoints:** 65+
**Database:** SQLite (embedded, no external DB needed)

## Overview

File Fridge is a Linux server application that automatically moves files to cold storage based on configurable criteria. It features a modern web dashboard, complete REST API, and background job scheduling for automatic file management.

**Key Features:**
- Path monitoring with find-compatible criteria (mtime, atime, size, name, permissions, etc.)
- Multiple operation types: MOVE, COPY, SYMLINK
- Scheduled automatic scanning with APScheduler
- File tagging system with automatic rule-based tagging
- Email and webhook notifications
- Multi-location cold storage support
- Progressive Web App (PWA) support
- Docker deployment with multi-platform builds

---

## Directory Structure

```
file-fridge/
├── app/                          # Main application code
│   ├── main.py                   # FastAPI entry point with lifespan management
│   ├── config.py                 # Pydantic settings (env vars + .env file)
│   ├── database.py               # SQLAlchemy setup and session management
│   ├── database_migrations.py    # Automatic schema migrations
│   ├── models.py                 # SQLAlchemy ORM models (12 models)
│   ├── schemas.py                # Pydantic request/response schemas
│   ├── routers/
│   │   ├── api/                  # REST API v1 endpoints (/api/v1/)
│   │   │   ├── paths.py          # MonitoredPath CRUD + scan triggers
│   │   │   ├── files.py          # File management (1300+ lines, largest)
│   │   │   ├── criteria.py       # File matching criteria
│   │   │   ├── tags.py           # File tagging system
│   │   │   ├── tag_rules.py      # Automated tagging rules
│   │   │   ├── stats.py          # Statistics endpoints
│   │   │   ├── storage.py        # Cold storage locations
│   │   │   ├── notifiers.py      # Notification configuration
│   │   │   └── cleanup.py        # Manual cleanup triggers
│   │   └── web/
│   │       └── views.py          # Web UI routes (Jinja2 templates)
│   ├── services/                 # Business logic layer
│   │   ├── file_workflow_service.py  # Core scanning/movement orchestration
│   │   ├── scheduler.py          # APScheduler background jobs
│   │   ├── file_mover.py         # File operation handlers (move/copy/symlink)
│   │   ├── file_thawer.py        # Restore from cold storage
│   │   ├── file_freezer.py       # Move to cold storage
│   │   ├── criteria_matcher.py   # Find-compatible pattern matching
│   │   ├── file_reconciliation.py # Database-filesystem sync
│   │   ├── notification_service.py # Email/webhook dispatcher
│   │   ├── tag_rule_service.py   # Automatic tagging logic
│   │   ├── scan_progress.py      # Real-time scan tracking
│   │   └── [8 additional services]
│   └── utils/
│       ├── network_detection.py  # Network mount detection
│       └── indexing.py           # .noindex file management (macOS)
├── templates/                    # Jinja2 HTML templates
│   ├── base.html                 # Main layout with navigation
│   ├── dashboard.html
│   ├── files.html
│   ├── paths/, storage/, criteria/, etc.
├── static/                       # Frontend assets
│   ├── css/style.css            # Custom styles (~1,500 lines)
│   ├── js/                       # Vanilla JavaScript (~6,024 lines)
│   ├── icons/                    # PWA icons
│   └── manifest.json            # PWA manifest
├── alembic/                      # Database migrations
│   ├── versions/                 # 6 migration files
│   └── alembic.ini
├── .github/workflows/
│   └── container-build-push.yml  # CI/CD: Docker multi-platform builds
├── Dockerfile                    # Multi-stage build (Python 3.12-slim)
├── docker-compose.yml
├── pyproject.toml               # Project metadata, dependencies, tools
├── requirements.txt             # Pinned dependencies
├── .env.example                 # Environment variable template
└── VERSION                      # Single version number file
```

---

## Tech Stack Deep Dive

### Backend
- **Framework:** FastAPI 0.109.0 (async ASGI web framework)
- **Server:** Uvicorn 0.27.0 (ASGI server)
- **ORM:** SQLAlchemy 2.0.25 (declarative models, async support)
- **Database:** SQLite (file-based, embedded)
- **Validation:** Pydantic 2.5.3 + Pydantic Settings 2.1.0
- **Scheduling:** APScheduler 3.10.4 (background jobs with persistence)
- **Async I/O:** aiofiles 23.2.1, aiosmtplib 3.0.1, httpx 0.26.0
- **Migrations:** Alembic 1.13.1 (version-controlled) + automatic runtime migrations

### Frontend
- **Templating:** Jinja2 3.1.6 (server-side rendering)
- **CSS Framework:** Bootstrap 5.3.0 (CDN)
- **Icons:** Bootstrap Icons 1.10.0
- **JavaScript:** Vanilla ES6+ (~6k lines, no frameworks)
- **PWA:** Service worker, manifest.json, installable

### DevOps
- **Container:** Docker (multi-stage, Python 3.12-slim-bookworm)
- **Package Manager:** uv (fast Python package installer)
- **CI/CD:** GitHub Actions (multi-platform: linux/amd64, linux/arm64)
- **Formatting:** Black (100-char line length)
- **Linting:** Ruff (extensive rule set)
- **Testing:** pytest (configured but no tests currently)

---

## Database Models and Relationships

### Core Models (12 total)

**1. MonitoredPath** - Directories being monitored
- **Fields:** id, name, source_path, operation_type (MOVE/COPY/SYMLINK), check_interval_seconds, enabled, prevent_indexing, error_message
- **Relationships:**
  - M2M: `storage_locations` (ColdStorageLocation)
  - 1:M: `criteria` (Criteria), `file_records` (FileRecord), `file_inventory` (FileInventory)
- **Backward Compatibility:** `cold_storage_path` property returns first storage location

**2. ColdStorageLocation** - Cold storage destinations
- **Fields:** id, name (unique, indexed), path
- **Relationships:** M2M: `paths` (MonitoredPath)

**3. Criteria** - File matching rules (defines what to KEEP in hot storage)
- **Fields:** id, path_id, criterion_type (MTIME/ATIME/CTIME/SIZE/NAME/INAME/TYPE/PERM/USER/GROUP), operator, value, enabled
- **Relationships:** M:1: `path` (MonitoredPath)
- **Important:** Files NOT matching criteria are moved to cold storage

**4. FileRecord** - Audit log of moved files
- **Fields:** id, path_id, original_path, cold_storage_path, cold_storage_location_id, file_size, moved_at, operation_type, criteria_matched
- **Indexes:** file_size, moved_at
- **Relationships:** M:1: `path`, `cold_storage_location`

**5. FileInventory** - Current filesystem inventory
- **Fields:** id, path_id, file_path, storage_type (HOT/COLD), file_size, file_mtime, file_atime, file_ctime, checksum (SHA256), file_extension, mime_type, status (ACTIVE/MOVED/DELETED/MISSING/MIGRATING), last_seen, cold_storage_location_id
- **Indexes:** file_path, file_size, mtime, atime, checksum, extension, storage_type, status, last_seen
- **Composite Indexes:** (path_id, storage_type, status), (storage_type, status, last_seen)
- **Relationships:** M:1: `path`, `cold_storage_location`; 1:M: `tags`

**6. PinnedFile** - Files excluded from future scans
- **Fields:** id, path_id, file_path, pinned_at, pinned_by
- **Index:** file_path

**7. Tag** - User-defined file categories
- **Fields:** id, name (unique, indexed), description, color (hex)
- **Relationships:** 1:M: `file_tags`

**8. FileTag** - File-to-Tag association
- **Fields:** id, file_id, tag_id, tagged_at, tagged_by
- **Indexes:** file_id, unique(file_id, tag_id)
- **Relationships:** M:1: `file`, `tag`

**9. TagRule** - Automatic tagging rules
- **Fields:** id, tag_id, criterion_type (EXTENSION/PATH_PATTERN/MIME_TYPE/SIZE/NAME_PATTERN), operator, value, enabled, priority
- **Index:** tag_id
- **Execution:** Priority-based (lower number = higher priority)

**10. Notifier** - Notification destinations
- **Fields:** id, name, type (EMAIL/GENERIC_WEBHOOK), address, enabled, filter_level (INFO/WARNING/ERROR)
- **SMTP Fields:** smtp_host, smtp_port, smtp_user, smtp_password, smtp_sender, smtp_use_tls

**11. Notification** - Event records
- **Fields:** id, level, message, created_at
- **Indexes:** level, created_at

**12. NotificationDispatch** - Dispatch audit log
- **Fields:** id, notification_id, notifier_id, status (SUCCESS/FAILED), details, timestamp
- **Index:** timestamp

### Database Migration Strategy
- **Automatic:** `database_migrations.py` runs on startup (idempotent)
- **Version-Controlled:** Alembic migrations in `alembic/versions/`
- **Safe:** Both systems coexist, idempotent operations

---

## Architecture and Design Patterns

### Layered Architecture
```
HTTP Request
    ↓
FastAPI Router (app/routers/api/*.py)
    ↓
Service Layer (app/services/*.py) - Business logic
    ↓
Database Layer (app/database.py + models.py) - SQLAlchemy ORM
    ↓
SQLite Database
```

### Key Patterns

**1. Dependency Injection**
```python
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/paths")
def list_paths(db: Session = Depends(get_db)):
    # db session injected automatically
```

**2. Service Abstraction**
- Business logic separated from HTTP handlers
- Services are pure Python (no FastAPI dependencies)
- Reusable across API and scheduler jobs

**3. Pydantic Validation**
- Request/response schemas in `schemas.py`
- Automatic validation and serialization
- Clear API contracts

**4. Background Jobs with APScheduler**
- Jobs stored in SQLite (persistence across restarts)
- Separate session factory for scheduler to avoid conflicts
- Job coalescing prevents overlapping scans

**5. Lifespan Management**
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: init database, start scheduler
    init_db()
    scheduler.start()
    yield
    # Shutdown: stop scheduler gracefully
    scheduler.shutdown()
```

**6. Error Handling**
- HTTPException for API errors with appropriate status codes
- Detailed error messages in response bodies
- Logging for debugging

**7. Async Support**
- async/await for I/O-bound operations
- aiofiles for file operations
- aiosmtplib for email sending
- httpx for webhooks

---

## Critical Workflows

### Workflow 1: Automatic File Scanning and Moving

```
1. APScheduler triggers scan job for MonitoredPath
   ↓
2. FileWorkflowService.process_path(path_id)
   ├─ Cleanup: Remove missing files from inventory
   ├─ Scan: Walk source_path, collect all files
   ├─ Update FileInventory (ACTIVE status, last_seen timestamp)
   ├─ For each file:
   │  ├─ Check if pinned (skip if yes)
   │  ├─ CriteriaMatcher.matches_criteria(file, criteria_list)
   │  ├─ If NOT matching criteria (file should move to cold):
   │  │  ├─ FileMover.move_file(file, cold_storage)
   │  │  ├─ Create FileRecord (audit log)
   │  │  └─ Update FileInventory (storage_type=COLD, status=MOVED)
   │  └─ TagRuleService.apply_rules_to_file(file)
   └─ Return scan results (files moved, size saved, etc.)
   ↓
3. NotificationService.send_notification(scan_results)
   ├─ Create Notification record
   └─ Dispatch to enabled Notifiers (email/webhook)
   ↓
4. Stats updated automatically for dashboard
```

### Workflow 2: Manual Freeze (API)

```
POST /api/v1/files/freeze/{inventory_id}
Body: {"storage_location_id": 1, "pin": true}
   ↓
files.py router endpoint
   ↓
FileThawer.freeze_file(inventory_id, storage_location_id, pin)
   ├─ Get file from FileInventory
   ├─ Select cold storage location
   ├─ FileMover.move_file(source, destination, operation_type)
   │  ├─ Check disk space
   │  ├─ Execute operation (move/copy/symlink)
   │  └─ Verify success
   ├─ Update FileInventory (storage_type=COLD)
   ├─ Create FileRecord
   ├─ If pin=true: Create PinnedFile
   └─ Return success response
```

### Workflow 3: Thaw (Restore from Cold Storage)

```
POST /api/v1/files/thaw/{inventory_id}
   ↓
FileThawer.thaw_file(inventory_id)
   ├─ Get file from FileInventory
   ├─ Restore to original_path
   ├─ Update FileInventory (storage_type=HOT, status=ACTIVE)
   └─ Return success
```

### Workflow 4: Tag Rule Application

```
TagRuleService.apply_rules_to_files(file_ids)
   ├─ Query TagRules (order by priority ASC)
   ├─ For each rule:
   │  └─ For each file:
   │     ├─ Evaluate criterion:
   │     │  ├─ EXTENSION: file.extension == rule.value
   │     │  ├─ PATH_PATTERN: glob/regex match on path
   │     │  ├─ MIME_TYPE: file.mime_type == rule.value
   │     │  ├─ SIZE: compare file.size with rule.value
   │     │  └─ NAME_PATTERN: glob/regex on filename
   │     └─ If match: Create FileTag (if not exists)
   └─ Return updated file objects with tags
```

---

## Key Development Conventions

### Code Style

**Formatting (Black):**
- Line length: 100 characters
- Target: Python 3.8-3.12
- Run: `black app/`

**Linting (Ruff):**
- Extensive rule set (E, F, W, I, N, UP, B, A, C4, DTZ, T10, EM, ISC, ICN, PIE, T20, PYI, PT, Q, RSE, RET, SIM, ARG, PTH, ERA, PD, PGH, PL, TRY, NPY, RUF)
- Line length: 100
- Ignore: E501 (line length, handled by Black)
- Run: `ruff check app/`

**Imports:**
- Standard library first
- Third-party second
- Local imports last
- Sorted alphabetically within groups

### Database Conventions

**Session Management:**
- Always use dependency injection: `db: Session = Depends(get_db)`
- Never create sessions manually in API endpoints
- Scheduler jobs use separate session factory: `get_db_for_scheduler()`

**Queries:**
- Use SQLAlchemy ORM (avoid raw SQL)
- Use `selectinload()` for eager loading relationships
- Add `.order_by()` for consistent results
- Use indexes for filtered columns

**Migrations:**
- Create Alembic migration for schema changes: `alembic revision --autogenerate -m "description"`
- Update `database_migrations.py` for critical changes (backward compatibility)
- Test migrations on fresh database
- Keep migrations idempotent

### API Conventions

**Endpoint Structure:**
- Base: `/api/v1/`
- Resource-based: `/api/v1/paths`, `/api/v1/files`
- Actions: POST `/api/v1/paths/{id}/scan`
- Use plural nouns for collections

**Request/Response:**
- Use Pydantic schemas for validation
- Return appropriate HTTP status codes:
  - 200: Success (GET, PUT, PATCH)
  - 201: Created (POST)
  - 204: No Content (DELETE)
  - 400: Bad Request (validation errors)
  - 404: Not Found
  - 500: Internal Server Error
- Error responses: `{"detail": "Error message"}`

**Pagination:**
- Query params: `skip` (offset), `limit` (page size)
- Default limit: 100
- Return total count in headers or response body

### Service Layer Conventions

**Service Functions:**
- Pure Python (no FastAPI imports)
- Accept db session as first parameter
- Return data objects, not HTTP responses
- Raise standard Python exceptions (caught by router)

**Error Handling:**
```python
from fastapi import HTTPException

# In service:
if not path:
    raise ValueError("Path not found")

# In router:
try:
    result = service_function(db, params)
except ValueError as e:
    raise HTTPException(status_code=404, detail=str(e))
```

### Frontend Conventions

**Templates:**
- Extend `base.html`
- Use Bootstrap 5 classes
- Load data via client-side API calls (not server-side)
- Keep templates simple (presentation only)

**JavaScript:**
- Vanilla JS (no frameworks)
- Use `fetch()` for API calls
- Handle errors gracefully
- Show loading states
- Use Bootstrap modals for confirmations

**CSS:**
- Use CSS variables for theming (see `static/css/style.css`)
- Follow Bootstrap conventions
- Mobile-first responsive design

---

## Environment Configuration

### Required Environment Variables

```bash
# Database
DATABASE_PATH=./data/file_fridge.db  # Default: ./data/file_fridge.db

# Logging
LOG_LEVEL=INFO                       # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_FILE_PATH=None                   # Optional log file path

# Application
APP_NAME=File Fridge                 # Custom branding
MAX_FILE_SIZE_MB=10240              # Max file size for operations (10GB)
DEFAULT_CHECK_INTERVAL=3600         # Default scan interval (1 hour)

# Data Retention
STATS_RETENTION_DAYS=30             # Stats data retention (days)

# Network Mounts
ALLOW_ATIME_OVER_NETWORK_MOUNTS=false  # Warn about atime on network mounts

# Docker Symlink Support (Docker deployments only)
CONTAINER_PATH_PREFIX=/hot-storage   # Path inside container
HOST_PATH_PREFIX=/mnt/storage       # Path on host (for symlinks)
```

### Configuration File: `.env`

Create from template:
```bash
cp .env.example .env
# Edit .env with your values
```

### Configuration Loading

```python
# app/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_path: str = "./data/file_fridge.db"
    log_level: str = "INFO"
    # ... other settings

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

settings = Settings()
```

---

## Common Development Tasks

### Setup Development Environment

**Using uv (recommended):**
```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and setup
git clone <repo>
cd file-fridge
uv sync

# Run development server
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Using pip:**
```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Add a New API Endpoint

1. **Define Pydantic schema** in `app/schemas.py`:
```python
class NewResourceCreate(BaseModel):
    name: str
    value: int
```

2. **Create database model** in `app/models.py` (if needed):
```python
class NewResource(Base):
    __tablename__ = "new_resources"
    id = Column(Integer, primary_key=True)
    name = Column(String, index=True)
    value = Column(Integer)
```

3. **Create migration**:
```bash
alembic revision --autogenerate -m "Add new_resources table"
alembic upgrade head
```

4. **Add endpoint** in `app/routers/api/new_resource.py`:
```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/api/v1/new-resources", tags=["new-resources"])

@router.post("/", response_model=schemas.NewResource, status_code=201)
def create_resource(resource: schemas.NewResourceCreate, db: Session = Depends(get_db)):
    db_resource = models.NewResource(**resource.dict())
    db.add(db_resource)
    db.commit()
    db.refresh(db_resource)
    return db_resource
```

5. **Register router** in `app/main.py`:
```python
from app.routers.api import new_resource
app.include_router(new_resource.router)
```

### Add a Background Job

1. **Create service function** in `app/services/my_service.py`:
```python
from app.database import get_db_for_scheduler

def my_background_task():
    db = get_db_for_scheduler()
    try:
        # Your task logic
        pass
    finally:
        db.close()
```

2. **Schedule job** in `app/services/scheduler.py`:
```python
from app.services.my_service import my_background_task

scheduler.add_job(
    my_background_task,
    trigger="interval",
    hours=1,
    id="my_task",
    replace_existing=True
)
```

### Add a Database Migration

```bash
# Auto-generate migration from model changes
alembic revision --autogenerate -m "Add new column to users"

# Review migration file in alembic/versions/
# Edit if needed

# Apply migration
alembic upgrade head

# Rollback if needed
alembic downgrade -1
```

### Run Tests

```bash
# Install dev dependencies
pip install pytest pytest-asyncio

# Run tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html
```

### Build and Run Docker Image

```bash
# Build
docker build -t file-fridge:latest .

# Run
docker run -d \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/hot-storage:/hot-storage \
  -v $(pwd)/cold-storage:/cold-storage \
  -e LOG_LEVEL=DEBUG \
  file-fridge:latest

# Or use docker-compose
docker-compose up -d
```

---

## API Reference Quick Guide

### Paths Management
- `GET /api/v1/paths` - List monitored paths
- `POST /api/v1/paths` - Create path
- `GET /api/v1/paths/{id}` - Get path details
- `PUT /api/v1/paths/{id}` - Update path
- `DELETE /api/v1/paths/{id}` - Delete path
- `POST /api/v1/paths/{id}/scan` - Trigger manual scan
- `GET /api/v1/paths/{id}/scan/progress` - Get scan progress

### Files Management
- `GET /api/v1/files` - List files (with filtering, sorting, pagination)
- `POST /api/v1/files/freeze/{inventory_id}` - Move to cold storage
- `POST /api/v1/files/thaw/{inventory_id}` - Restore from cold
- `POST /api/v1/files/relocate/{inventory_id}` - Move between cold locations
- `POST /api/v1/files/{inventory_id}/pin` - Pin file (exclude from scans)
- `DELETE /api/v1/files/{inventory_id}/pin` - Unpin file
- Bulk operations: `/bulk/freeze`, `/bulk/thaw`, `/bulk/pin`, `/bulk/unpin`

### Criteria Management
- `GET /api/v1/criteria/path/{path_id}` - List criteria for path
- `POST /api/v1/criteria/path/{path_id}` - Create criteria
- `PUT /api/v1/criteria/{id}` - Update criteria
- `DELETE /api/v1/criteria/{id}` - Delete criteria

### Storage Locations
- `GET /api/v1/storage-locations` - List cold storage locations
- `POST /api/v1/storage-locations` - Create location
- `GET /api/v1/storage-locations/{id}` - Get location details
- `PUT /api/v1/storage-locations/{id}` - Update location
- `DELETE /api/v1/storage-locations/{id}` - Delete location
- `GET /api/v1/storage-locations/{id}/stats` - Get location statistics

### Tags
- `GET /api/v1/tags` - List tags
- `POST /api/v1/tags` - Create tag
- `POST /api/v1/tags/files/{file_id}/tags` - Add tag to file
- `DELETE /api/v1/tags/files/{file_id}/tags/{tag_id}` - Remove tag
- `POST /api/v1/tags/bulk/add` - Bulk add tags
- `POST /api/v1/tags/bulk/remove` - Bulk remove tags

### Statistics
- `GET /api/v1/stats` - Basic statistics
- `GET /api/v1/stats/detailed` - Comprehensive metrics
- `POST /api/v1/stats/cleanup` - Manual cleanup of old data

### Notifiers
- `GET /api/v1/notifiers` - List notifiers
- `POST /api/v1/notifiers` - Create notifier
- `POST /api/v1/notifiers/{id}/test` - Test notifier

---

## Important Concepts and Edge Cases

### 1. Criteria Logic (CRITICAL)

**Criteria define what files to KEEP in hot storage (active files), NOT what to move to cold.**

```python
# Example: "Keep files accessed in the last 3 minutes"
Criteria:
  criterion_type = ATIME
  operator = <
  value = 3

# Files with atime >= 3 minutes will be moved to cold storage
# Files with atime < 3 minutes will stay in hot storage
```

**All criteria must match** for a file to be considered "active" (kept in hot storage).

### 2. Scan Interval Best Practices

**Rule of Thumb:** Scan interval should be ≤ 1/3 of your smallest time-based criterion threshold.

```
Time criterion < 3 min   → Scan every 60 seconds (1 min)
Time criterion < 60 min  → Scan every 300-600 seconds (5-10 min)
Time criterion < 1 day   → Scan every 1800-3600 seconds (30-60 min)
```

**Minimum scan interval:** 60 seconds

### 3. Access Time (atime) Considerations

**File Fridge does NOT update atime when scanning** (uses stat() only).

**Filesystem mount options:**
- `relatime` (default on modern Linux) - Recommended
- `strictatime` - Updates on every access (performance impact)
- `noatime` - Never updates (DO NOT USE with atime-based criteria)

**macOS Special Handling:**
- Uses both `atime` and Spotlight "Last Open" time
- Takes most recent of the two
- If "Last Open" is None → treated as "infinitely old" (epoch time)
- Ensures never-opened files are moved to cold storage

**Network Mounts:**
- Verify atime is enabled on server
- Consider using `mtime` if atime is unreliable
- Set `ALLOW_ATIME_OVER_NETWORK_MOUNTS=false` to get warnings

### 4. Multi-Storage Location Support

**Current State:**
- MonitoredPath can have multiple ColdStorageLocation (M2M)
- `path.cold_storage_path` property returns first location (backward compatibility)

**Known TODOs (from code comments):**
- `file_scanner.py`: Scan all storage locations for inventory
- `file_reconciliation.py`: Check all storage locations
- `criteria.py`: Validate atime for all locations
- `indexing.py`: Manage .noindex for all locations

**When working with storage locations:**
- Prefer using `path.storage_locations` (list)
- Avoid relying on `path.cold_storage_path` for new features

### 5. Symlink Operations in Docker

**Problem:** Symlinks created inside container point to container paths, not host paths.

**Solution:** Path translation via environment variables:
```bash
CONTAINER_PATH_PREFIX=/hot-storage
HOST_PATH_PREFIX=/mnt/server/storage
```

**Example:**
```
Container path: /hot-storage/file.txt
Host path: /mnt/server/storage/file.txt
Symlink created: /mnt/server/storage/file.txt → /cold-storage/file.txt
```

See `app/services/file_mover.py:_translate_path_for_symlink()`

### 6. File Status Lifecycle

```
ACTIVE → File exists in hot storage
  ↓ (criteria not matched)
MIGRATING → Moving to cold storage (temporary)
  ↓
MOVED → File in cold storage
  ↓ (thaw operation)
ACTIVE → File restored to hot storage

MISSING → File should exist but not found (orphaned record)
DELETED → File was intentionally deleted
```

### 7. Pinned Files

**Purpose:** Exclude specific files from future scans (prevent re-evaluation).

**Use Cases:**
- Files that shouldn't be moved but don't match criteria
- Temporary exclusions during testing
- VIP files that must stay in hot storage

**Implementation:**
```python
# Pin file
pinned_file = PinnedFile(
    path_id=path_id,
    file_path="/path/to/file.txt",
    pinned_by="admin"
)
db.add(pinned_file)

# File scanner checks:
if is_file_pinned(db, file_path):
    continue  # Skip this file
```

### 8. Network Mount Detection

**Location:** `app/utils/network_detection.py`

**Current Implementation:** Heuristic-based (checks for `/Volumes`, `/mnt`, `/net`)

**TODO (from code):** Use `statfs` API instead of heuristics

**Usage:**
```python
from app.utils.network_detection import is_network_mount

if is_network_mount("/mnt/server/share"):
    # Warn about atime reliability
    logger.warning("Path is on network mount, atime may be unreliable")
```

### 9. Preventing macOS Spotlight Indexing

**Purpose:** Prevent Spotlight from indexing cold storage (performance).

**Implementation:** Create `.noindex` file in storage directory.

**Location:** `app/utils/indexing.py`

**Usage:**
```python
from app.utils.indexing import ensure_noindex

ensure_noindex("/cold-storage")
# Creates /cold-storage/.noindex
```

**Configuration:** Set `prevent_indexing=True` on MonitoredPath.

### 10. Notification System

**Levels:**
- INFO: Regular operations (scan completed)
- WARNING: Non-critical issues (low disk space)
- ERROR: Critical issues (scan failed)

**Filtering:**
- Each Notifier has `filter_level`
- Only notifications ≥ filter_level are sent
- Example: filter_level=WARNING → receives WARNING and ERROR, not INFO

**Email Configuration:**
```python
{
  "type": "EMAIL",
  "address": "admin@example.com",
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 587,
  "smtp_user": "sender@gmail.com",
  "smtp_password": "app_password",
  "smtp_sender": "File Fridge <sender@gmail.com>",
  "smtp_use_tls": true,
  "filter_level": "WARNING"
}
```

**Webhook Configuration:**
```python
{
  "type": "GENERIC_WEBHOOK",
  "address": "https://hooks.slack.com/services/XXX/YYY/ZZZ",
  "filter_level": "ERROR"
}
```

---

## Testing Guidelines

### Test Structure (Recommended)

```
tests/
├── conftest.py              # Pytest fixtures
├── test_models.py           # Database model tests
├── test_criteria_matcher.py # Criteria matching logic
├── test_file_mover.py       # File operations
├── test_api/
│   ├── test_paths.py
│   ├── test_files.py
│   └── test_tags.py
└── test_services/
    ├── test_file_workflow.py
    └── test_scheduler.py
```

### Fixtures (conftest.py)

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base

@pytest.fixture(scope="function")
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

@pytest.fixture
def test_client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)
```

### Example Test

```python
def test_create_path(test_client):
    response = test_client.post("/api/v1/paths", json={
        "name": "Test Path",
        "source_path": "/tmp/test",
        "operation_type": "move",
        "check_interval_seconds": 3600,
        "enabled": True
    })
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Path"
    assert "id" in data
```

---

## CI/CD Pipeline

### GitHub Actions Workflow

**File:** `.github/workflows/container-build-push.yml`

**Triggers:**
- Push to `main` branch
- Git tags matching `v*`
- Manual workflow dispatch

**Build Steps:**
1. Checkout code
2. Set up Docker Buildx
3. Extract version from `VERSION` file
4. Determine Docker tags:
   - `latest`
   - Git tag (if tag push)
   - VERSION file version
   - Commit SHA (7 chars)
5. Authenticate with Docker Hub
6. Build and push multi-platform images:
   - `linux/amd64`
   - `linux/arm64`
7. Use registry cache for faster rebuilds

**Docker Tags Example:**
```
Push to main:
  - yourrepo/file-fridge:latest
  - yourrepo/file-fridge:0.0.22
  - yourrepo/file-fridge:abc1234

Tag v1.0.0:
  - yourrepo/file-fridge:latest
  - yourrepo/file-fridge:v1.0.0
  - yourrepo/file-fridge:0.0.22
  - yourrepo/file-fridge:abc1234
```

---

## Known Issues and TODOs

### From Code Review

1. **Multi-Storage Location Refactoring** (Priority: Medium)
   - `MonitoredPath.cold_storage_path` property is temporary
   - Services need updating to handle multiple locations:
     - [ ] `file_scanner.py`: Scan all storage locations
     - [ ] `file_reconciliation.py`: Check all locations
     - [ ] `criteria.py`: Validate atime for all locations
     - [ ] `indexing.py`: Manage .noindex for all locations

2. **Network Mount Detection** (Priority: Low)
   - `app/utils/network_detection.py` TODO: Use `statfs` API instead of heuristics
   - Current implementation: checks for common mount paths

3. **Test Coverage** (Priority: High)
   - No tests currently in repository
   - Recommended: Start with API endpoint tests
   - Use pytest with fixtures for database

4. **Path Migration Service** (Priority: Low)
   - `app/services/path_migration.py` exists but needs testing
   - Handles moving files between paths

5. **Documentation** (Priority: Medium)
   - Add API documentation (Swagger/OpenAPI auto-generated at `/docs`)
   - Add developer setup guide
   - Add deployment best practices

---

## Security Considerations

**Current State:**
- No authentication (assumes trusted network)
- Path validation to prevent directory traversal
- File operations respect system permissions
- Non-root user in Docker (user: `filefridge`)

**Recommendations for Production:**

1. **Add Authentication:**
   - Use reverse proxy (nginx, Caddy) with authentication
   - Or implement FastAPI security (OAuth2, JWT)

2. **Use HTTPS:**
   - Terminate SSL at reverse proxy
   - Use Let's Encrypt for certificates

3. **Implement RBAC:**
   - User roles: Admin, Operator, Viewer
   - Endpoint permissions based on role

4. **Audit Logging:**
   - Log who did what and when
   - Retain logs for compliance

5. **Encrypt Sensitive Data:**
   - SMTP passwords in database
   - Consider using secrets manager (HashiCorp Vault, AWS Secrets Manager)

6. **Input Validation:**
   - Already using Pydantic schemas (good!)
   - Review path traversal prevention

7. **Rate Limiting:**
   - Prevent API abuse
   - Use SlowAPI or similar middleware

---

## Useful Commands Reference

### Development

```bash
# Start dev server with auto-reload
uvicorn app.main:app --reload

# Format code
black app/

# Lint code
ruff check app/

# Fix linting issues
ruff check app/ --fix

# Run tests
pytest

# Run tests with coverage
pytest --cov=app

# Create migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback migration
alembic downgrade -1
```

### Docker

```bash
# Build image
docker build -t file-fridge:dev .

# Run container
docker run -d -p 8000:8000 file-fridge:dev

# View logs
docker logs -f <container_id>

# Execute shell in container
docker exec -it <container_id> /bin/bash

# Use docker-compose
docker-compose up -d
docker-compose logs -f
docker-compose down
```

### Database

```bash
# Access SQLite database
sqlite3 data/file_fridge.db

# Common queries
sqlite> .tables
sqlite> .schema monitored_paths
sqlite> SELECT * FROM monitored_paths;
sqlite> SELECT COUNT(*) FROM file_inventory;
sqlite> .quit
```

### API Testing (curl)

```bash
# List paths
curl http://localhost:8000/api/v1/paths

# Create path
curl -X POST http://localhost:8000/api/v1/paths \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test",
    "source_path": "/tmp/test",
    "operation_type": "move",
    "check_interval_seconds": 3600,
    "enabled": true
  }'

# Trigger scan
curl -X POST http://localhost:8000/api/v1/paths/1/scan

# Get statistics
curl http://localhost:8000/api/v1/stats/detailed
```

---

## Performance Optimization Tips

### Database

1. **Add Indexes:**
   - Add indexes for frequently filtered columns
   - Use composite indexes for multi-column filters
   - Example: `Index('idx_file_path_status', 'file_path', 'status')`

2. **Use Eager Loading:**
   ```python
   from sqlalchemy.orm import selectinload

   paths = db.query(MonitoredPath)\
       .options(selectinload(MonitoredPath.criteria))\
       .all()
   ```

3. **Limit Query Results:**
   ```python
   files = db.query(FileInventory)\
       .limit(100)\
       .offset(skip)\
       .all()
   ```

### File Operations

1. **Batch Operations:**
   - Process files in batches (e.g., 100 files at a time)
   - Commit database changes in batches

2. **Use ThreadPoolExecutor:**
   ```python
   from concurrent.futures import ThreadPoolExecutor

   with ThreadPoolExecutor(max_workers=4) as executor:
       futures = [executor.submit(process_file, f) for f in files]
   ```

3. **Disk Space Pre-Checks:**
   - Always check available space before operations
   - See `app/services/file_mover.py:check_disk_space()`

### API

1. **Use Pagination:**
   - Default limit: 100
   - Allow client to specify limit (max: 1000)

2. **Return Only Needed Fields:**
   ```python
   class FileListItem(BaseModel):
       id: int
       file_path: str
       file_size: int
       # Omit large fields like checksum for list views
   ```

3. **Cache Static Data:**
   - Tag list
   - Storage location list
   - Use HTTP cache headers

---

## Troubleshooting Common Issues

### Issue: "Database is locked"

**Cause:** SQLite doesn't handle concurrent writes well.

**Solutions:**
- Ensure only one instance is running
- Scheduler uses separate session factory (already implemented)
- Consider PostgreSQL for high-concurrency deployments

### Issue: "Files not being moved"

**Checklist:**
1. Is the path enabled? (`enabled=True`)
2. Do criteria exist for the path?
3. Are criteria enabled? (`enabled=True`)
4. Is scan interval appropriate for criteria?
5. Check logs for errors
6. Manually trigger scan: `POST /api/v1/paths/{id}/scan`

### Issue: "atime not updating"

**Checklist:**
1. Check filesystem mount options: `mount | grep <path>`
2. Look for `noatime` (bad for atime-based criteria)
3. `relatime` is recommended
4. For network mounts, verify atime support on server

### Issue: "Permission denied during file operations"

**Checklist:**
1. Check file ownership: `ls -l <file>`
2. Check directory permissions
3. Ensure app user has read/write access
4. In Docker: check volume mount permissions

### Issue: "Symlinks pointing to wrong paths in Docker"

**Solution:** Configure path translation:
```bash
CONTAINER_PATH_PREFIX=/hot-storage
HOST_PATH_PREFIX=/mnt/server/storage
```

See: `docs/DOCKER.md#symlink-operations-in-docker`

---

## Additional Resources

### Documentation Files
- `README.md` - User-facing documentation
- `DOCKER.md` - Docker deployment guide
- `docs/CONFIGURATION_GUIDE.md` - Configuration best practices
- `docs/ATIME_VERIFICATION.md` - Script to verify atime behavior

### API Documentation
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

### External Links
- FastAPI Documentation: https://fastapi.tiangolo.com/
- SQLAlchemy Documentation: https://docs.sqlalchemy.org/
- Pydantic Documentation: https://docs.pydantic.dev/
- APScheduler Documentation: https://apscheduler.readthedocs.io/

---

## Version History

**Current Version:** 0.0.22 (Beta)

**Recent Changes:** (based on git commits)
- Added `cold_storage_location_id` column to file tables
- Enhanced file management and statistics features
- Improved files table performance
- Added notification system (email + webhooks)
- Implemented tag system and tag rules

---

## Contact and Support

For issues, questions, or contributions:
- **Repository:** See README.md for repository URL
- **Issues:** GitHub Issues
- **License:** Apache License 2.0

---

**Last Updated:** 2026-01-17
**Document Version:** 1.0
**Maintained By:** AI Assistant for File Fridge Project
