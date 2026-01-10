"""File browser web routes - serves templated HTML, data loaded via API."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/files", response_class=HTMLResponse)
async def browse_files(request: Request):
    """File browser - serves templated HTML, data loaded via API."""
    return templates.TemplateResponse("files.html", {
        "request": request,
        "active_page": "files"
    })

