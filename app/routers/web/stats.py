"""Statistics web routes - uses API to fetch data."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.templating import Jinja2Templates

router = APIRouter()
# Templates directory relative to project root
templates = Jinja2Templates(directory="app/templates")


@router.get("/stats", response_class=HTMLResponse)
async def statistics(request: Request):
    """Statistics overview - data loaded via API."""
    return templates.TemplateResponse("stats/overview.html", {
        "request": request,
        "active_page": "stats"
    })

