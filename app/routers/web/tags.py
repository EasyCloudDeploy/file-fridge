"""Tag management web routes - serves templated HTML, data loaded via API."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/tags", response_class=HTMLResponse)
async def tags(request: Request):
    """Tag management page - serves templated HTML, data loaded via API."""
    return templates.TemplateResponse("tags.html", {
        "request": request,
        "active_page": "tags"
    })
