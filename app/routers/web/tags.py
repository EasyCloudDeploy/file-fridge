"""Tag management web routes - serves static HTML, data loaded via API."""
from fastapi import APIRouter
from fastapi.responses import FileResponse
from pathlib import Path

router = APIRouter()


@router.get("/tags")
async def tags():
    """Tag management page - serves static HTML, data loaded via API."""
    html_path = Path("static/html/tags.html")
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    else:
        # Fallback if static file doesn't exist
        from fastapi.responses import HTMLResponse
        return HTMLResponse(
            content="<h1>Tags</h1><p>Static HTML file not found. Please check static/html/tags.html</p>",
            status_code=404
        )
