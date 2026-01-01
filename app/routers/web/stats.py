"""Statistics web routes - serves static HTML, data loaded via API."""
from fastapi import APIRouter
from fastapi.responses import FileResponse
from pathlib import Path

router = APIRouter()


@router.get("/stats")
async def statistics():
    """Statistics overview - serves static HTML, data loaded via API."""
    html_path = Path("static/html/stats.html")
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    else:
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content="<h1>Statistics</h1><p>Static HTML file not found. Please check static/html/stats.html</p>", status_code=404)

