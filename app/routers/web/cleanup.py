"""File cleanup web routes - redirects to API."""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.post("/cleanup")
async def cleanup_files(request: Request):
    """Cleanup - handled by JavaScript/AJAX."""
    # This route is now handled by JavaScript/AJAX in the template
    return RedirectResponse(url="/files", status_code=303)


@router.post("/cleanup/duplicates")
async def cleanup_duplicate_files(request: Request):
    """Duplicate cleanup - handled by JavaScript/AJAX."""
    # This route is now handled by JavaScript/AJAX in the template
    return RedirectResponse(url="/files", status_code=303)

