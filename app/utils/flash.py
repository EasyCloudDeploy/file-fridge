"""Flash message utilities for session-based messaging."""

from typing import Optional

from starlette.requests import Request


def get_flash(request: Request, key: str = "message") -> Optional[str]:
    """Get and remove a flash message from the session."""
    if "flash" not in request.session:
        request.session["flash"] = {}
    flash_data = request.session.get("flash", {})
    message = flash_data.pop(key, None)
    if message:
        request.session["flash"] = flash_data
    return message


def set_flash(request: Request, message: str, key: str = "message"):
    """Set a flash message in the session."""
    if "flash" not in request.session:
        request.session["flash"] = {}
    request.session["flash"][key] = message


def set_error(request: Request, error: str):
    """Set an error flash message."""
    set_flash(request, error, "error")
