"""File thawing web routes - no longer needed, handled by JavaScript."""
from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.post("/files/{file_id}/thaw")
async def thaw_file(file_id: int):
    """Thaw a file - handled by JavaScript, redirect to files page."""
    # This route is kept for backwards compatibility but should not be used
    # JavaScript handles the API call directly
    return RedirectResponse(url="/files", status_code=303)

