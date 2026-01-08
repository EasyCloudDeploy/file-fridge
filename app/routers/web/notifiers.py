"""Notifier management web routes - serves static HTML, data loaded via API."""
from fastapi import APIRouter
from fastapi.responses import FileResponse
from pathlib import Path

router = APIRouter()


@router.get("/notifiers")
async def notifiers():
    """Notifier management page - serves static HTML, data loaded via API."""
    html_path = Path("static/html/notifiers.html")
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    else:
        # Fallback if static file doesn't exist
        from fastapi.responses import HTMLResponse
        return HTMLResponse(
            content="<h1>Notifiers</h1><p>Static HTML file not found. Please check static/html/notifiers.html</p>",
            status_code=404
        )
