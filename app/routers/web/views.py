"""Consolidated Web UI routes - serves templated HTML, data loaded via API."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")


# Simple template routes (path, template, active_page)
TEMPLATE_ROUTES = [
    ("/", "dashboard.html", "dashboard"),
    ("/files", "files.html", "files"),
    ("/paths", "paths/list.html", "paths"),
    ("/paths/new", "paths/form.html", "paths"),
    ("/stats", "stats.html", "stats"),
    ("/storage-locations", "storage/list.html", "storage"),
    ("/storage-locations/new", "storage/form.html", "storage"),
    ("/tags", "tags.html", "tags"),
    ("/notifiers", "notifiers.html", "notifiers"),
    ("/settings", "settings.html", "settings"),
    ("/login", "login.html", None),  # Login page (no active page highlight)
]


# Register simple template routes
def _create_route_handler(template: str, active_page: str):
    """Create a route handler for a template."""

    async def handler(request: Request):
        return templates.TemplateResponse(
            template, {"request": request, "active_page": active_page}
        )

    return handler


for path, template, active_page in TEMPLATE_ROUTES:
    handler = _create_route_handler(template, active_page)
    router.add_api_route(path, handler, methods=["GET"], response_class=HTMLResponse)


# Dynamic routes with path parameters
@router.get("/paths/{path_id}", response_class=HTMLResponse)
async def get_path(request: Request, path_id: int):
    """Path details page."""
    return templates.TemplateResponse(
        "paths/detail.html", {"request": request, "active_page": "paths"}
    )


@router.get("/paths/{path_id}/edit", response_class=HTMLResponse)
async def edit_path_form(request: Request, path_id: int):
    """Edit path form."""
    return templates.TemplateResponse(
        "paths/form.html", {"request": request, "active_page": "paths"}
    )


@router.get("/paths/{path_id}/criteria/new", response_class=HTMLResponse)
async def create_criteria_form(request: Request, path_id: int):
    """Create criteria form."""
    return templates.TemplateResponse(
        "criteria/form.html", {"request": request, "active_page": "paths"}
    )


@router.get("/criteria/{criteria_id}/edit", response_class=HTMLResponse)
async def edit_criteria_form(request: Request, criteria_id: int):
    """Edit criteria form."""
    return templates.TemplateResponse(
        "criteria/form.html", {"request": request, "active_page": "paths"}
    )


@router.get("/storage-locations/{location_id}", response_class=HTMLResponse)
async def get_storage_location(request: Request, location_id: int):
    """Storage location details page."""
    return templates.TemplateResponse(
        "storage/detail.html", {"request": request, "active_page": "storage"}
    )


@router.get("/storage-locations/{location_id}/edit", response_class=HTMLResponse)
async def edit_storage_location_form(request: Request, location_id: int):
    """Edit storage location form."""
    return templates.TemplateResponse(
        "storage/form.html", {"request": request, "active_page": "storage"}
    )


# Legacy redirect routes (kept for backwards compatibility)
@router.post("/cleanup")
async def cleanup_redirect():
    """Legacy cleanup redirect."""
    return RedirectResponse(url="/files", status_code=303)


@router.post("/cleanup/duplicates")
async def cleanup_duplicates_redirect():
    """Legacy cleanup duplicates redirect."""
    return RedirectResponse(url="/files", status_code=303)


@router.post("/files/{file_id}/thaw")
async def thaw_redirect(file_id: int):
    """Legacy thaw redirect."""
    return RedirectResponse(url="/files", status_code=303)


@router.get("/remote-files/{connection_id}", response_class=HTMLResponse)
async def get_remote_files(request: Request, connection_id: int) -> HTMLResponse:
    """Remote files browser page."""
    return templates.TemplateResponse(
        "remote_files.html",
        {"request": request, "active_page": "remote-files", "connection_id": connection_id},
    )
