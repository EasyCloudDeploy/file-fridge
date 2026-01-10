"""Criteria management web routes - serves templated HTML, data loaded via API."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/paths/{path_id}/criteria/new", response_class=HTMLResponse)
async def create_criteria_form(request: Request, path_id: int):
    """Show create criteria form - serves templated HTML, data loaded via API."""
    return templates.TemplateResponse("criteria/form.html", {
        "request": request,
        "active_page": "paths"
    })


@router.get("/criteria/{criteria_id}/edit", response_class=HTMLResponse)
async def edit_criteria_form(request: Request, criteria_id: int):
    """Show edit criteria form - serves templated HTML, data loaded via API."""
    return templates.TemplateResponse("criteria/form.html", {
        "request": request,
        "active_page": "paths"
    })

