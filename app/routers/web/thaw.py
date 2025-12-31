"""File thawing web routes - redirects to API."""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from app.utils.flash import set_flash, set_error

router = APIRouter()


@router.post("/files/{file_id}/thaw")
async def thaw_file(request: Request, file_id: int):
    """Thaw a file - redirects to API endpoint."""
    # This route is now handled by JavaScript/AJAX in the template
    # Redirect to files page - the JavaScript will handle the API call
    return RedirectResponse(url="/files", status_code=303)

