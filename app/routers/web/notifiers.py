"""Notifier management web routes - serves templated HTML, data loaded via API."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/notifiers", response_class=HTMLResponse)
async def notifiers(request: Request):
    """Notifier management page - serves templated HTML, data loaded via API."""
    return templates.TemplateResponse("notifiers.html", {
        "request": request,
        "active_page": "notifiers"
    })
