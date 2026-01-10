"""Storage location management web routes - serves templated HTML, data loaded via API."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/storage-locations", response_class=HTMLResponse)
async def list_storage_locations(request: Request):
    """List all storage locations - serves templated HTML, data loaded via API."""
    return templates.TemplateResponse("storage/list.html", {
        "request": request,
        "active_page": "storage"
    })


@router.get("/storage-locations/new", response_class=HTMLResponse)
async def create_storage_location_form(request: Request):
    """Show create storage location form - serves templated HTML."""
    return templates.TemplateResponse("storage/form.html", {
        "request": request,
        "active_page": "storage"
    })


@router.get("/storage-locations/{location_id}", response_class=HTMLResponse)
async def get_storage_location(request: Request, location_id: int):
    """Get storage location details - serves templated HTML, data loaded via API."""
    return templates.TemplateResponse("storage/detail.html", {
        "request": request,
        "active_page": "storage"
    })


@router.get("/storage-locations/{location_id}/edit", response_class=HTMLResponse)
async def edit_storage_location_form(request: Request, location_id: int):
    """Show edit storage location form - serves templated HTML, data loaded via API."""
    return templates.TemplateResponse("storage/form.html", {
        "request": request,
        "active_page": "storage"
    })
