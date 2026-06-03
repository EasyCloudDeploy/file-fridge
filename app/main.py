"""Main FastAPI application entry point."""

import logging
import os

# Configure logging BEFORE importing any app modules that create loggers
# Get config from environment variables directly to avoid circular import
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH")

handlers = [logging.StreamHandler()]  # Always log to stdout

# Add file logging if LOG_FILE_PATH is set
if LOG_FILE_PATH:
    handlers.append(logging.FileHandler(LOG_FILE_PATH, mode="a"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=handlers,
    force=True,  # Force reconfiguration even if logging was already initialized
)

logger = logging.getLogger(__name__)
logger.info("Logging configured: level=%s, file=%s", LOG_LEVEL, LOG_FILE_PATH or "stdout only")

# Now import everything else after logging is configured
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database import SessionLocal, has_schema_objects, init_db
from app.database_migrations import run_startup_migrations
from app.frontend_assets import configure_templates
from app.models import FileInventory, FileStatus, RelocationTask, RelocationTaskStatus
from app.routers.api import auth as api_auth
from app.routers.api import browser as api_browser
from app.routers.api import cleanup as api_cleanup
from app.routers.api import criteria as api_criteria
from app.routers.api import encryption as api_encryption
from app.routers.api import files as api_files
from app.routers.api import identity as api_identity
from app.routers.api import migrations as api_migrations
from app.routers.api import notifiers as api_notifiers
from app.routers.api import p2p as api_p2p
from app.routers.api import paths as api_paths
from app.routers.api import remote as api_remote
from app.routers.api import settings as api_settings  # noqa: E402
from app.routers.api import stats as api_stats
from app.routers.api import storage as api_storage
from app.routers.api import tag_rules as api_tag_rules
from app.routers.api import tags as api_tags
from app.routers.api import users as api_users
from app.routers.web.views import router as web_router
from app.security import PermissionChecker
from app.services.file_cleanup import FileCleanup
from app.services.p2p_service import p2p_service
from app.services.scheduler import scheduler_service

# Apply filter to uvicorn access logger
# TODO: Implement RemoteReceiveFilter to reduce log noise from /receive endpoint
# logging.getLogger("uvicorn.access").addFilter(RemoteReceiveFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Application lifespan manager."""
    # Startup
    logger.info(f"Starting File Fridge with DATABASE_PATH: {settings.database_path}")

    if has_schema_objects():
        logger.info("Existing database detected; deferring schema changes to Alembic")
    else:
        logger.info("Initializing database...")
        init_db()

    logger.info("Running database migrations...")
    run_startup_migrations()

    # Clean up symlink entries from inventory (one-time cleanup for existing databases)
    logger.info("Cleaning up symlink entries from file inventory...")
    try:
        db = SessionLocal()
        try:
            results = FileCleanup.cleanup_symlink_inventory_entries(db)
            if results["removed"] > 0:
                logger.info(
                    f"Removed {results['removed']} symlink entries from inventory "
                    f"(checked {results['checked']} entries)"
                )
            else:
                logger.info("No symlink entries found in inventory")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Error during symlink cleanup: {e!s}")
        # Don't fail startup if cleanup fails

    # Reset files stuck in MIGRATING status from interrupted freeze/thaw operations.
    # RelocationTask-based migrations are recovered separately in relocation_manager._recover_tasks().
    # Here we handle files whose MIGRATING status was set by the freeze/thaw workflow but never
    # cleared (e.g., due to an application crash mid-operation).
    logger.info("Checking for files stuck in MIGRATING status...")
    try:
        db = SessionLocal()
        try:
            active_relocation_ids = {
                row[0]
                for row in db.query(RelocationTask.inventory_id)
                .filter(
                    RelocationTask.status.in_(
                        [RelocationTaskStatus.PENDING, RelocationTaskStatus.RUNNING]
                    )
                )
                .all()
            }
            stuck_files = (
                db.query(FileInventory).filter(FileInventory.status == FileStatus.MIGRATING).all()
            )
            recovered = 0
            for f in stuck_files:
                if f.id not in active_relocation_ids:
                    f.status = FileStatus.ACTIVE
                    recovered += 1
            if recovered:
                db.commit()
                logger.warning(
                    f"Reset {recovered} file(s) from MIGRATING to ACTIVE (interrupted freeze/thaw)"
                )
            else:
                logger.info("No stuck MIGRATING files found")
        finally:
            db.close()
    except SQLAlchemyError as e:
        logger.warning(f"Error during MIGRATING file recovery: {e}")

    logger.info("Starting P2P node runtime...")
    try:
        p2p_service.start_node()
    except Exception as e:
        logger.warning(f"Error starting P2P runtime: {e!s}")

    logger.info("Starting scheduler...")
    scheduler_service.start()

    yield

    # Shutdown
    logger.info("Stopping scheduler...")
    scheduler_service.stop()
    p2p_service.stop_node()
    logger.info("Application shutdown complete")


# Create FastAPI app
app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)


# Add global exception handler to log unhandled exceptions
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch and log all unhandled exceptions."""
    logger.exception(
        f"Unhandled exception occurred: {exc!r}\n"
        f"Request: {request.method} {request.url}\n"
        f"Client: {request.client.host if request.client else 'unknown'}"
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error occurred. Check logs for details."},
    )


# Configure Jinja2 Templates
templates = configure_templates(Jinja2Templates(directory="templates"))

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include authentication router (public - no authentication required)
app.include_router(api_auth.router)

# Protected API routers - require authentication and RBAC
app.include_router(api_browser.router, dependencies=[Depends(PermissionChecker("browser"))])
app.include_router(api_paths.router, dependencies=[Depends(PermissionChecker("paths"))])
app.include_router(api_criteria.router, dependencies=[Depends(PermissionChecker("criteria"))])
app.include_router(api_files.router, dependencies=[Depends(PermissionChecker("files"))])
app.include_router(api_stats.router, dependencies=[Depends(PermissionChecker("stats"))])
app.include_router(api_cleanup.router, dependencies=[Depends(PermissionChecker("cleanup"))])
app.include_router(api_tags.router, dependencies=[Depends(PermissionChecker("tags"))])
app.include_router(api_tag_rules.router, dependencies=[Depends(PermissionChecker("tag-rules"))])
app.include_router(api_storage.router, dependencies=[Depends(PermissionChecker("storage"))])
app.include_router(api_storage.public_router)
app.include_router(api_notifiers.router, dependencies=[Depends(PermissionChecker("notifiers"))])
app.include_router(api_settings.router, dependencies=[Depends(PermissionChecker("notifiers"))])
app.include_router(api_encryption.router, dependencies=[Depends(PermissionChecker("Encryption"))])
app.include_router(api_migrations.router, dependencies=[Depends(PermissionChecker("migrations"))])
app.include_router(api_users.router)  # Roles handled inside this router
app.include_router(api_identity.router)  # Permissions handled inside router
app.include_router(api_p2p.router)
app.include_router(api_remote.router)

# Include consolidated web router (public - frontend handles auth)
app.include_router(web_router)


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "version": settings.app_version, "app_name": settings.app_name}
