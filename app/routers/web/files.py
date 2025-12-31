"""File browser web routes."""
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from starlette.templating import Jinja2Templates
from typing import Optional
from app.utils.flash import get_flash
from app.routers.api import files as api_files, paths as api_paths

router = APIRouter()
# Templates directory relative to project root
templates = Jinja2Templates(directory="app/templates")


@router.get("/files", response_class=HTMLResponse)
async def browse_files(
    request: Request,
    path_id: Optional[int] = Query(None)
):
    """File browser - uses API to fetch data."""
    # Get flash messages from session
    error = get_flash(request, "error")
    message = get_flash(request, "message")
    
    return templates.TemplateResponse("files/browser.html", {
        "request": request,
        "active_page": "files",
        "selected_path_id": path_id,
        "error": error,
        "message": message
    })

