"""Main FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import SessionLocal, init_db
from app.database_migrations import run_startup_migrations
from app.routers.api import auth as api_auth
from app.routers.api import browser as api_browser
from app.routers.api import encryption as api_encryption
from app.routers.api import cleanup as api_cleanup
from app.routers.api import criteria as api_criteria
from app.routers.api import files as api_files
from app.routers.api import notifiers as api_notifiers
from app.routers.api import paths as api_paths
from app.routers.api import remote as api_remote
from app.routers.api import stats as api_stats
from app.routers.api import storage as api_storage
from app.routers.api import tag_rules as api_tag_rules
from app.routers.api import tags as api_tags
from app.routers.web.views import router as web_router
from app.security import get_current_user
from app.services.file_cleanup import FileCleanup
from app.services.scheduler import scheduler_service

# Configure logging
handlers = [logging.StreamHandler()]  # Always log to stdout

# Add file logging if LOG_FILE_PATH is set
if settings.log_file_path:
    handlers.append(logging.FileHandler(settings.log_file_path, mode="a"))

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=handlers,
)

logger = logging.getLogger(__name__)


# Filter out /api/remote/receive from uvicorn access logs to prevent spam during file transfers
class RemoteReceiveFilter(logging.Filter):
    """Filter to suppress /api/remote/receive endpoint logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False to suppress log record."""
        return "/api/remote/receive" not in record.getMessage()


# Apply filter to uvicorn access logger
logging.getLogger("uvicorn.access").addFilter(RemoteReceiveFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Application lifespan manager."""
    # Startup
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

    logger.info("Starting scheduler...")
    scheduler_service.start()

    yield

    # Shutdown
    logger.info("Stopping scheduler...")
    scheduler_service.stop()
    logger.info("Application shutdown complete")


# Create FastAPI app
app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

# Configure Jinja2 templates
templates = Jinja2Templates(directory="templates")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include authentication router (public - no authentication required)
app.include_router(api_auth.router)

# Protected API routers - require authentication
api_dependencies = [Depends(get_current_user)]
app.include_router(api_browser.router, dependencies=api_dependencies)
app.include_router(api_paths.router, dependencies=api_dependencies)
app.include_router(api_criteria.router, dependencies=api_dependencies)
app.include_router(api_files.router, dependencies=api_dependencies)
app.include_router(api_stats.router, dependencies=api_dependencies)
app.include_router(api_cleanup.router, dependencies=api_dependencies)
app.include_router(api_tags.router, dependencies=api_dependencies)
app.include_router(api_tag_rules.router, dependencies=api_dependencies)
app.include_router(api_storage.router, dependencies=api_dependencies)
app.include_router(api_notifiers.router, dependencies=api_dependencies)
app.include_router(api_encryption.router, dependencies=api_dependencies)
app.include_router(api_remote.router)

# Include consolidated web router (public - frontend handles auth)
app.include_router(web_router)


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "version": settings.app_version, "app_name": settings.app_name}


def main():
    """Main entry point for the application."""
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
