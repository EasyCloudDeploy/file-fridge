"""Main FastAPI application entry point."""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.config import settings
from app.database import init_db
from app.routers.api import paths as api_paths, criteria as api_criteria, files as api_files, stats as api_stats, cleanup as api_cleanup
from app.routers.web import dashboard, paths as web_paths, files as web_files, stats as web_stats, criteria as web_criteria, thaw as web_thaw, cleanup as web_cleanup
from app.services.scheduler import scheduler_service

# Configure logging
handlers = [logging.StreamHandler()]  # Always log to stdout

# Add file logging if LOG_FILE_PATH is set
if settings.log_file_path:
    handlers.append(logging.FileHandler(settings.log_file_path, mode='a'))

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=handlers
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("Initializing database...")
    init_db()
    
    logger.info("Starting scheduler...")
    scheduler_service.start()
    
    yield
    
    # Shutdown
    logger.info("Stopping scheduler...")
    scheduler_service.stop()
    logger.info("Application shutdown complete")


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include routers
app.include_router(api_paths.router)
app.include_router(api_criteria.router)
app.include_router(api_files.router)
app.include_router(api_stats.router)
app.include_router(api_cleanup.router)

app.include_router(dashboard.router)
app.include_router(web_paths.router)
app.include_router(web_files.router)
app.include_router(web_stats.router)
app.include_router(web_criteria.router)
app.include_router(web_thaw.router)
app.include_router(web_cleanup.router)


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": settings.app_version,
        "app_name": settings.app_name
    }


def main():
    """Main entry point for the application."""
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )


if __name__ == "__main__":
    main()

