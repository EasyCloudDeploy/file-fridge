"""Path management web routes - serves templated HTML, data loaded via API."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/paths", response_class=HTMLResponse)
async def list_paths(request: Request):
    """List all monitored paths - serves templated HTML, data loaded via API."""
    return templates.TemplateResponse("paths/list.html", {
        "request": request,
        "active_page": "paths"
    })


@router.get("/paths/new", response_class=HTMLResponse)
async def create_path_form(request: Request):
    """Show create path form - serves templated HTML."""
    return templates.TemplateResponse("paths/form.html", {
        "request": request,
        "active_page": "paths"
    })


@router.get("/paths/{path_id}", response_class=HTMLResponse)
async def get_path(request: Request, path_id: int):
    """Get path details - serves templated HTML, data loaded via API."""
    return templates.TemplateResponse("paths/detail.html", {
        "request": request,
        "active_page": "paths"
    })


@router.get("/paths/{path_id}/edit", response_class=HTMLResponse)
async def edit_path_form(request: Request, path_id: int):
    """Show edit path form - serves templated HTML, data loaded via API."""
    return templates.TemplateResponse("paths/form.html", {
        "request": request,
        "active_page": "paths"
    })

