"""Statistics web routes - serves templated HTML, data loaded via API."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/stats", response_class=HTMLResponse)
async def statistics(request: Request):
    """Statistics overview - serves templated HTML, data loaded via API."""
    return templates.TemplateResponse("stats.html", {
        "request": request,
        "active_page": "stats"
    })

