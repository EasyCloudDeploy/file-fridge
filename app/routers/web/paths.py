"""Path management web routes - serves static HTML, data loaded via API."""
from fastapi import APIRouter
from fastapi.responses import FileResponse
from pathlib import Path as PathLib

router = APIRouter()


@router.get("/paths")
async def list_paths():
    """List all monitored paths - serves static HTML, data loaded via API."""
    html_path = PathLib("static/html/paths/list.html")
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    else:
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content="<h1>Paths</h1><p>Static HTML file not found.</p>", status_code=404)


@router.get("/paths/new")
async def create_path_form():
    """Show create path form - serves static HTML."""
    html_path = PathLib("static/html/paths/form.html")
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    else:
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content="<h1>Create Path</h1><p>Static HTML file not found.</p>", status_code=404)


@router.get("/paths/{path_id}")
async def get_path(path_id: int):
    """Get path details - serves static HTML, data loaded via API."""
    html_path = PathLib("static/html/paths/detail.html")
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    else:
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content="<h1>Path Details</h1><p>Static HTML file not found.</p>", status_code=404)


@router.get("/paths/{path_id}/edit")
async def edit_path_form(path_id: int):
    """Show edit path form - serves static HTML, data loaded via API."""
    html_path = PathLib("static/html/paths/form.html")
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    else:
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content="<h1>Edit Path</h1><p>Static HTML file not found.</p>", status_code=404)

