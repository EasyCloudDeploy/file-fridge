"""Main FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import init_db
from app.database_migrations import run_startup_migrations
from app.routers.api import cleanup as api_cleanup
from app.routers.api import criteria as api_criteria
from app.routers.api import files as api_files
from app.routers.api import notifiers as api_notifiers
from app.routers.api import paths as api_paths
from app.routers.api import stats as api_stats
from app.routers.api import storage as api_storage
from app.routers.api import tag_rules as api_tag_rules
from app.routers.api import tags as api_tags
from app.routers.web.views import router as web_router
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("Initializing database...")
    init_db()

    logger.info("Running database migrations...")
    run_startup_migrations()

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

# Include API routers
app.include_router(api_paths.router)
app.include_router(api_criteria.router)
app.include_router(api_files.router)
app.include_router(api_stats.router)
app.include_router(api_cleanup.router)
app.include_router(api_tags.router)
app.include_router(api_tag_rules.router)
app.include_router(api_storage.router)
app.include_router(api_notifiers.router)

# Include consolidated web router
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
