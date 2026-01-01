"""Criteria management web routes - serves static HTML, data loaded via API."""
from fastapi import APIRouter
from fastapi.responses import FileResponse
from pathlib import Path

router = APIRouter()


@router.get("/paths/{path_id}/criteria/new")
async def create_criteria_form(path_id: int):
    """Show create criteria form - serves static HTML, data loaded via API."""
    html_path = Path("static/html/criteria/form.html")
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    else:
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content="<h1>Add Criteria</h1><p>Static HTML file not found.</p>", status_code=404)


@router.get("/criteria/{criteria_id}/edit")
async def edit_criteria_form(criteria_id: int):
    """Show edit criteria form - serves static HTML, data loaded via API."""
    html_path = Path("static/html/criteria/form.html")
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    else:
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content="<h1>Edit Criteria</h1><p>Static HTML file not found.</p>", status_code=404)

