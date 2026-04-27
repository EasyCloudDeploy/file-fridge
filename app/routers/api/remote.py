"""Legacy remote API compatibility layer.

All previous HTTP signature-based remote-transfer endpoints are deprecated and
incompatible with File Fridge P2P v2.
"""

from fastapi import APIRouter, HTTPException

from app.constants import RESOURCE_REMOTE_CONNECTIONS

router = APIRouter(prefix="/api/v1/remote", tags=[RESOURCE_REMOTE_CONNECTIONS])

LEGACY_DETAIL = (
    "Legacy remote protocol has been removed in this release. "
    "Upgrade all nodes to P2P v2 and use /api/v1/p2p endpoints."
)


@router.api_route("", methods=["GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS"])
@router.api_route(
    "/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS"]
)
def legacy_remote_protocol_removed(path: str = ""):
    _ = path
    raise HTTPException(status_code=426, detail=LEGACY_DETAIL)
