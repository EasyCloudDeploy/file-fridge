"""File cleanup web routes - no longer needed, handled by JavaScript."""
from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()

@router.post("/cleanup")
async def cleanup_files():
    """Manually trigger cleanup of missing files - handled by JavaScript."""
    # This route is kept for backwards compatibility but should not be used
    # JavaScript handles the API call directly
    return RedirectResponse(url="/files", status_code=303)


@router.post("/cleanup/duplicates")
async def cleanup_duplicate_files():
    """Manually trigger cleanup of duplicate file records - handled by JavaScript."""
    # This route is kept for backwards compatibility but should not be used
    # JavaScript handles the API call directly
    return RedirectResponse(url="/files", status_code=303)

