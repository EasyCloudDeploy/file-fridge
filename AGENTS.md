# AGENTS.md - AI Agent Guide for File Fridge

## Overview

This document provides guidelines for **AI agents** (Claude Code, GitHub Copilot, Cursor, Aider, etc.) working with the File Fridge codebase. It complements `CLAUDE.md` with agent-specific workflows, best practices, and automation guidelines.

**Target Audience:** AI coding assistants, autonomous agents, and AI-powered development tools

---

## Quick Reference for Agents

**Project Type:** UV-based Python FastAPI application
**Agent-Friendly Features:**
- ✅ Comprehensive API documentation at `/docs` (Swagger)
- ✅ Type hints throughout codebase (Pydantic schemas)
- ✅ Clear separation of concerns (routers → services → database)
- ✅ Auto-formatted with Black (100 chars)
- ✅ Linted with Ruff (extensive rules)
- ✅ Well-documented models and schemas

**Key Files for Context:**
- `CLAUDE.md` - Comprehensive codebase documentation
- `README.md` - User-facing documentation
- `app/models.py` - Database schema (24 models)
- `app/schemas.py` - API contracts (Pydantic)
- `pyproject.toml` - Project metadata and dependencies

---

## Critical Agent Rules

### 🚨 Non-Negotiable Requirements

**1. Package Manager - UV Only**
```bash
# ✅ CORRECT - Use UV for all Python operations
uv run uvicorn app.main:app --reload
uv run pytest
uv run alembic upgrade head

# ❌ WRONG - Never use pip
pip install package-name  # FORBIDDEN
python -m pip install     # FORBIDDEN
```

**2. Database Changes - SQLAlchemy Auto-Sync First**

**Simple schema changes use SQLAlchemy `create_all()` (preferred):**
- ✅ Adding new tables
- ✅ Adding new columns to existing tables  
- ✅ Adding new indexes
- ✅ Creating relationships/foreign keys

**Complex migrations use Alembic (when needed):**
- 🔧 Column type conversions (e.g., INTEGER → VARCHAR)
- 🔧 Column/table renames
- 🔧 Data migrations or backfills
- 🔧 Multi-step schema changes
- 🔧 Changes not supported by SQLite's `ALTER TABLE`

```bash
# ✅ For simple changes (new tables/columns/indexes):
# Just update models.py - SQLAlchemy auto-sync handles it on next startup
uv run python -c "from app.database import init_db; init_db()"  # Test locally

# ✅ For complex migrations only:
uv run alembic revision --autogenerate -m "Convert column type"
uv run alembic upgrade head
```

**Why this approach?**
- SQLite supports `CREATE TABLE` and `ALTER TABLE ADD COLUMN` natively
- SQLAlchemy's `create_all()` is idempotent and handles these automatically
- Faster iteration during development
- Alembic reserved for operations SQLite cannot handle natively

**3. Code Quality - Format and Lint**
```bash
# ✅ Run before committing
uv run black app/
uv run ruff check app/ --fix

# Agent should auto-format code to match project standards
```

---

## Agent Workflows

### Workflow 1: Adding a New Feature

**Context Gathering:**
1. Read `CLAUDE.md` for architecture overview
2. Check `app/models.py` for relevant database models
3. Review `app/schemas.py` for existing API contracts
4. Examine similar endpoints in `app/routers/api/`

**Implementation Steps:**
1. **Design**: Plan the feature (models, API, services)
2. **Models**: Update `app/models.py` if database changes needed
3. **Test Schema Changes** (if step 2 applies):
   ```bash
   # For new tables/columns/indexes - SQLAlchemy auto-sync
   uv run python -c "from app.database import init_db; init_db()"
   
   # For complex changes (renames, type conversions) - Alembic
   uv run alembic revision --autogenerate -m "Add feature X tables"
   uv run alembic upgrade head
   ```
4. **Schemas**: Add Pydantic schemas to `app/schemas.py`
5. **Service**: Implement business logic in `app/services/`
6. **Router**: Add API endpoints in `app/routers/api/`
7. **Register**: Include router in `app/main.py`
8. **Format**: Run Black and Ruff
9. **Test**: Manual testing or add pytest tests
10. **Commit**: Clear, descriptive commit message

**Example Agent Prompt Response:**
```
I'll add the new user authentication feature. Here's my implementation plan:

1. Database changes needed:
   - New table: User (id, username, password_hash, created_at)
   - New table: Session (id, user_id, token, expires_at)

2. Creating migration:
   [Shows alembic command]

3. Adding Pydantic schemas:
   - UserCreate, UserResponse, LoginRequest, LoginResponse

4. Implementing auth service:
   - Password hashing with bcrypt
   - Token generation with secrets

5. Adding API endpoints:
   - POST /api/v1/auth/register
   - POST /api/v1/auth/login
   - GET /api/v1/auth/me

[Proceeds with implementation...]
```

### Workflow 2: Debugging an Issue

**Context Gathering:**
1. Read error message/stack trace completely
2. Locate relevant code files
3. Check recent git commits for related changes
4. Review database schema if DB-related
5. Check logs in `data/` directory

**Investigation Steps:**
1. **Reproduce**: Understand how to trigger the issue
2. **Isolate**: Identify the specific function/service causing it
3. **Root Cause**: Trace back to the source
4. **Fix**: Implement minimal change to resolve
5. **Verify**: Test the fix doesn't break other functionality
6. **Format**: Run Black/Ruff
7. **Commit**: Describe issue and fix

**Agent Communication:**
```
I found the issue in app/services/file_workflow_service.py:142

The problem is that we're not handling the case where a file
is deleted between the scan and the move operation. This causes
a FileNotFoundError.

Fix: Add try-except around the move operation and mark the
file as MISSING in the database.

[Shows code diff]

This maintains data integrity while gracefully handling the
edge case.
```

### Workflow 3: Refactoring Code

**Before Refactoring:**
1. **Understand**: Fully comprehend current implementation
2. **Test Coverage**: Note existing behavior (or add tests)
3. **Impact Analysis**: Identify all callers/dependencies
4. **Plan**: Document what changes and why

**Refactoring Steps:**
1. **Preserve Behavior**: Ensure same inputs → same outputs
2. **Incremental**: Small, testable changes
3. **No Feature Creep**: Refactor OR add features, not both
4. **Format**: Black/Ruff after each change
5. **Verify**: Check nothing broke
6. **Commit**: Separate refactoring commits from features

**Agent Best Practices:**
- Don't refactor and add features simultaneously
- Don't "improve" code that wasn't asked to be changed
- Keep changes focused and minimal
- Maintain backward compatibility

### Workflow 4: Writing Documentation

**Documentation Types:**

**Code Comments:**
```python
# ✅ GOOD - Explain WHY, not WHAT
# Use Spotlight metadata as fallback for atime on macOS
# because some filesystems don't update atime reliably
last_open = get_spotlight_last_open(file_path)

# ❌ BAD - Obvious what the code does
# Get the last open time from Spotlight
last_open = get_spotlight_last_open(file_path)
```

**Docstrings:**
```python
# ✅ GOOD - For public APIs and complex logic
def freeze_file(inventory_id: int, storage_location_id: int, pin: bool = False):
    """
    Move a file from hot storage to cold storage.

    Args:
        inventory_id: ID of file in FileInventory
        storage_location_id: Target cold storage location
        pin: If True, pin the file to prevent future auto-moves

    Returns:
        Updated FileInventory object

    Raises:
        ValueError: If file not found or already in cold storage
        IOError: If move operation fails
    """

# ❌ BAD - Unnecessary for simple CRUD
def get_path(db: Session, path_id: int):
    """Get a path by ID."""  # Too obvious, skip it
```

**README/Guides:**
- User-facing: Focus on HOW to use
- Developer docs: Focus on WHY it works this way

---

## Agent Best Practices

### Code Generation Guidelines

**1. Type Hints Always**
```python
# ✅ GOOD - Full type hints
def process_file(file_path: str, config: dict[str, Any]) -> FileRecord:
    ...

# ❌ BAD - No type hints
def process_file(file_path, config):
    ...
```

**2. Use Pydantic for Validation**
```python
# ✅ GOOD - Pydantic schema
class PathCreate(BaseModel):
    name: str
    source_path: str
    operation_type: OperationType

# ❌ BAD - Manual validation
def create_path(name, source_path, operation_type):
    if not name:
        raise ValueError("Name required")
    ...
```

**3. Dependency Injection**
```python
# ✅ GOOD - FastAPI dependency injection
@router.get("/paths")
def list_paths(db: Session = Depends(get_db)):
    ...

# ❌ BAD - Creating session manually
@router.get("/paths")
def list_paths():
    db = SessionLocal()  # Don't do this
    ...
```

**4. Service Layer Abstraction**
```python
# ✅ GOOD - Business logic in service
# app/services/path_service.py
def get_active_paths(db: Session) -> list[MonitoredPath]:
    return db.query(MonitoredPath).filter(
        MonitoredPath.enabled == True
    ).all()

# app/routers/api/paths.py
@router.get("/paths/active")
def list_active_paths(db: Session = Depends(get_db)):
    return path_service.get_active_paths(db)

# ❌ BAD - Business logic in router
@router.get("/paths/active")
def list_active_paths(db: Session = Depends(get_db)):
    return db.query(MonitoredPath).filter(
        MonitoredPath.enabled == True
    ).all()
```

**5. Error Handling**
```python
# ✅ GOOD - Specific exceptions with context
from fastapi import HTTPException

try:
    file = db.query(FileInventory).get(file_id)
    if not file:
        raise HTTPException(
            status_code=404,
            detail=f"File {file_id} not found"
        )
except IOError as e:
    raise HTTPException(
        status_code=500,
        detail=f"Failed to move file: {str(e)}"
    )

# ❌ BAD - Bare except, generic errors
try:
    file = db.query(FileInventory).get(file_id)
except:
    raise HTTPException(status_code=500, detail="Error")
```

### Code Style Compliance

**Automatic Formatting:**
Agents should auto-format code to match project style:

```python
# Project uses Black with 100-char line length
# Agent should automatically format to:

def long_function_name(
    parameter_one: str,
    parameter_two: int,
    parameter_three: dict[str, Any],
) -> Optional[FileRecord]:
    """Properly formatted function signature."""
    pass
```

**Import Organization:**
```python
# Standard library
import os
from datetime import datetime
from pathlib import Path

# Third-party
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

# Local
from app import models, schemas
from app.database import get_db
from app.services import file_service
```

### Testing Guidelines

**When to Write Tests:**
- Critical business logic (file operations, criteria matching)
- Complex algorithms (checksumming, deduplication)
- Edge cases (race conditions, error handling)

**Test Structure:**
```python
# tests/test_services/test_criteria_matcher.py

def test_mtime_criterion_match(db_session):
    """Test that files older than threshold match MTIME criterion."""
    # Arrange
    old_file = create_test_file(mtime=datetime.now() - timedelta(days=10))
    criterion = Criteria(criterion_type="MTIME", operator=">", value="7")

    # Act
    matches = criteria_matcher.matches_criteria(old_file, [criterion])

    # Assert
    assert matches is True

def test_mtime_criterion_no_match(db_session):
    """Test that recent files don't match MTIME criterion."""
    # Arrange
    new_file = create_test_file(mtime=datetime.now() - timedelta(days=3))
    criterion = Criteria(criterion_type="MTIME", operator=">", value="7")

    # Act
    matches = criteria_matcher.matches_criteria(new_file, [criterion])

    # Assert
    assert matches is False
```

---

## Common Agent Tasks

### Task 1: Add API Endpoint for Feature X

**Agent Checklist:**
- [ ] Read CLAUDE.md for architecture patterns
- [ ] Check if database changes needed
- [ ] Test schema changes locally (SQLAlchemy auto-sync for new tables/columns)
- [ ] Create Alembic migration only for complex changes (renames, type conversions, data migrations)
- [ ] Add Pydantic schemas
- [ ] Implement service function
- [ ] Add router endpoint
- [ ] Register router in main.py
- [ ] Run Black and Ruff
- [ ] Test manually or add pytest test
- [ ] Update API documentation (if needed)

### Task 2: Fix Bug in File Processing

**Agent Checklist:**
- [ ] Reproduce the bug
- [ ] Locate the faulty code
- [ ] Understand the root cause
- [ ] Implement minimal fix
- [ ] Add test to prevent regression
- [ ] Run Black and Ruff
- [ ] Verify fix doesn't break existing functionality
- [ ] Document fix in commit message

### Task 3: Optimize Database Query

**Agent Checklist:**
- [ ] Profile current query performance
- [ ] Identify bottleneck (N+1, missing index, etc.)
- [ ] Plan optimization (eager loading, index, caching)
- [ ] Implement change
- [ ] Create migration for new indexes (if needed)
- [ ] Verify same results, better performance
- [ ] Run Black and Ruff
- [ ] Document optimization in comments

### Task 4: Refactor Service Layer

**Agent Checklist:**
- [ ] Identify code smell or duplication
- [ ] Plan refactoring approach
- [ ] Ensure tests exist (or add them)
- [ ] Refactor incrementally
- [ ] Run tests after each change
- [ ] Run Black and Ruff
- [ ] Verify no behavior changes
- [ ] Commit with clear refactoring message

---

## Agent Communication Guidelines

### Explaining Changes

**Good Communication:**
```
I've implemented the bulk file tagging feature:

1. Database changes:
   - No schema changes needed (uses existing FileTag table)

2. New endpoint: POST /api/v1/tags/bulk/add
   - Request: {"file_ids": [1,2,3], "tag_id": 5}
   - Response: {"tagged_count": 3, "failed_count": 0}

3. Service implementation:
   - Validates all file_ids exist
   - Checks tag exists
   - Bulk inserts FileTag records (ignores duplicates)
   - Returns success count

4. Testing:
   - Tested with 100 files successfully
   - Handles duplicate tags gracefully
   - Returns 404 if tag doesn't exist

Code is formatted with Black and passes Ruff checks.
```

**Poor Communication:**
```
Added bulk tagging. Should work fine.
```

### Asking for Clarification

**Good Questions:**
```
Before implementing user authentication, I need clarification:

1. Should we use JWT tokens or session-based auth?
2. Where should passwords be hashed - in the service or router?
3. Do you want email verification for new users?
4. Should we add rate limiting for login attempts?

Based on your answers, I can implement the appropriate solution.
```

**Poor Questions:**
```
How should I do auth?
```

### Reporting Blockers

**Good Blocker Report:**
```
I've encountered a blocker with the file relocation feature:

Issue: FileInventory.cold_storage_location_id is nullable, but the
       relocation logic assumes it's always set for COLD files.

Impact: 15% of files in the test database have storage_type='COLD'
        but cold_storage_location_id=NULL (legacy data).

Options:
1. Backfill NULL values with a default location (breaking change)
2. Handle NULL gracefully (check and warn)
3. Create migration to make field required + backfill

Recommendation: Option 2 for backward compatibility.

Waiting for your decision before proceeding.
```

**Poor Blocker Report:**
```
Can't relocate files. Database problem. What should I do?
```

---

## Project-Specific Agent Knowledge

### Database Workflow Decision Criteria

**When to Use SQLAlchemy Auto-Sync vs Alembic:**

```
SQLAlchemy Auto-Sync (Simple Changes):
├── Adding NEW tables → Use init_db()
├── Adding NEW columns → Use init_db()
├── Adding NEW indexes → Use init_db()  
└── Creating relationships/foreign keys → Use init_db()

Alembic Migrations (Complex Changes):
├── Renaming columns → Alembic required
├── Renaming tables → Alembic required
├── Changing column types → Alembic required
├── Data migrations/backfills → Alembic required
├── Dropping columns → Alembic required
└── Multi-step schema changes → Alembic required
```

**SQLite-Specific Capabilities:**

SQLite natively supports:
- ✅ `CREATE TABLE` (new tables)
- ✅ `ALTER TABLE ADD COLUMN` (new columns, with limitations)
- ✅ `CREATE INDEX` (new indexes)

SQLite does NOT support:
- ❌ `ALTER TABLE DROP COLUMN` (until very recent versions)
- ❌ `ALTER TABLE RENAME COLUMN` (before SQLite 3.25.0)
- ❌ Most other `ALTER TABLE` operations

**Why Auto-Sync Works:**
SQLAlchemy's `Base.metadata.create_all()` uses `CREATE TABLE IF NOT EXISTS` and handles `ALTER TABLE ADD COLUMN` where supported. For simple additive changes, this is idempotent and production-safe.

**Testing Schema Changes:**
```bash
# Test that your model changes work with auto-sync
uv run python -c "from app.database import init_db; init_db()"

# For complex migrations that require Alembic
uv run alembic revision --autogenerate -m "Description"
uv run alembic upgrade head
uv run alembic downgrade -1  # Test rollback
```

### Critical Business Logic

**1. Criteria Matching (INVERSE LOGIC)**
```
IMPORTANT: Criteria define what files to KEEP in hot storage.
Files NOT matching criteria are moved to cold storage.

Example:
  Criterion: atime < 3 minutes
  Result: Files accessed in last 3 min STAY hot
          Files NOT accessed in last 3 min → MOVE to cold
```

**2. File Status Lifecycle**
```
ACTIVE → file in hot storage, active
MIGRATING → currently being moved (temporary state)
MOVED → file successfully moved to cold storage
MISSING → file record exists but file not found
DELETED → file was intentionally deleted
```

**3. Multi-Storage Location Support**
```
Current state: MonitoredPath has M2M with ColdStorageLocation
Backward compat: path.cold_storage_path returns first location
TODO: Several services need updating for full multi-location support
```

**4. macOS-Specific Handling**
```
- Spotlight "Last Open" time used as fallback for atime
- .noindex files created to prevent Spotlight indexing cold storage
- Network mount detection uses heuristics (TODO: use statfs API)
```

**5. Docker Symlink Translation**
```
Container paths must be translated to host paths for symlinks:
  CONTAINER_PATH_PREFIX=/hot-storage
  HOST_PATH_PREFIX=/mnt/server/storage

Symlink created with host path, not container path.
```

### Common Pitfalls for Agents

**Pitfall 1: Using Alembic When Not Needed**
```
❌ Agent adds new table/column to models.py
❌ Creates unnecessary Alembic migration
❌ Commits redundant migration file

Result: Cluttered migration history, slower development!

✅ Agent adds new table/column to models.py
✅ Tests with SQLAlchemy auto-sync: uv run python -c "from app.database import init_db; init_db()"
✅ Only creates Alembic migration for complex changes (renames, type conversions, data backfills)
✅ Commits model changes (and migration only if needed)
```

**Pitfall 2: Using pip Instead of uv**
```
❌ User: "install pytest"
❌ Agent: pip install pytest

✅ User: "install pytest"
✅ Agent: This project uses UV. Running: uv add pytest
```

**Pitfall 3: Over-Engineering**
```
❌ User: "add logging to the file mover"
❌ Agent: Creates logging service, config system, formatters,
         rotating file handlers, log aggregation, etc.

✅ User: "add logging to the file mover"
✅ Agent: Adds logger.info() calls at key points using
         existing logging setup
```

**Pitfall 4: Breaking Backward Compatibility**
```
❌ Agent renames MonitoredPath.cold_storage_path property
Result: All users' code using this property breaks

✅ Agent adds new property but keeps old one for compatibility
✅ Adds deprecation warning to old property
✅ Documents migration path in CHANGELOG
```

**Pitfall 5: Ignoring Code Style**
```
❌ Agent generates code with 120-char lines,
   inconsistent imports, no type hints

✅ Agent generates code matching Black/Ruff standards
✅ Runs formatters before presenting code
✅ Follows existing patterns in codebase
```

---

## Agent Self-Check Questions

Before completing any task, agents should verify:

**Code Quality:**
- [ ] Does code follow Black formatting (100 chars)?
- [ ] Does code pass Ruff linting?
- [ ] Are type hints present on all functions?
- [ ] Are imports organized correctly?

**Architecture:**
- [ ] Is business logic in services, not routers?
- [ ] Are Pydantic schemas used for validation?
- [ ] Is dependency injection used for DB sessions?
- [ ] Does code follow existing patterns?

**Database:**
- [ ] Did I determine if the change needs Alembic or SQLAlchemy auto-sync?
- [ ] Did I test the schema change locally with `init_db()`?
- [ ] Did I create an Alembic migration only for complex changes (if needed)?
- [ ] Did I test the migration upgrade path (if using Alembic)?
- [ ] Is the migration reversible (downgrade) (if using Alembic)?
- [ ] Did I commit the migration file (if created)?

**Testing:**
- [ ] Did I test the changes manually?
- [ ] Should I add automated tests?
- [ ] Did I verify no existing functionality broke?

**Documentation:**
- [ ] Are complex logic decisions documented?
- [ ] Did I update API docs if needed?
- [ ] Is my commit message clear and descriptive?

**Compatibility:**
- [ ] Is this backward compatible?
- [ ] Will existing users be able to upgrade?
- [ ] Did I preserve existing APIs?

---

## Integration with Other Agents

### GitHub Copilot
- Use this file as context for code suggestions
- Follow patterns from CLAUDE.md
- Respect UV and Alembic requirements

### Cursor AI
- Load CLAUDE.md and AGENTS.md for context
- Use codebase search for similar implementations
- Follow project conventions

### Aider
- Reference CLAUDE.md for architecture
- Use `uv run` for all commands
- Create migrations for DB changes

### ChatGPT/Claude in IDE
- Request CLAUDE.md content for context
- Ask for specific patterns before implementing
- Verify changes against project standards

---

## Version History

**Current Version:** 1.0
**Last Updated:** 2026-01-17
**Target Project Version:** File Fridge 0.0.22 (Beta)

---

## See Also

- `CLAUDE.md` - Comprehensive codebase documentation
- `GITHUB_AGENTS.md` - GitHub automation documentation
- `README.md` - User-facing documentation
- `/docs` - API documentation (Swagger UI)

---

**Note to Agents:** This document is maintained alongside the codebase. When project conventions change, this file should be updated to reflect them. Always verify information against the actual codebase if in doubt.
