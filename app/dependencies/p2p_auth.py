"""FastAPI dependencies for authenticated P2P peer endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import P2PNetworkConfig
from app.services.p2p_service import p2p_service
from app.utils.p2p_auth import LEGACY_PSK_HEADER, MAX_P2P_BODY_BYTES, verify_request


def require_p2p_network(db: Session) -> P2PNetworkConfig:
    """Return enabled P2P network config or raise HTTP errors."""
    config = p2p_service.get_network_config(db)
    if not config or not config.enabled:
        raise HTTPException(status_code=503, detail="P2P network is disabled")
    return config


def require_p2p_psk(config: P2PNetworkConfig) -> str:
    """Return decrypted PSK for auth or raise when unavailable."""
    try:
        return p2p_service.get_network_psk(config)
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="P2P authentication key is unavailable",
        ) from exc


async def _read_bounded_body(request: Request) -> bytes:
    """Read the request body with an explicit size bound for peer auth."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
        if declared_length > MAX_P2P_BODY_BYTES:
            raise HTTPException(status_code=413, detail="P2P request body too large")

    body = await request.body()
    if len(body) > MAX_P2P_BODY_BYTES:
        raise HTTPException(status_code=413, detail="P2P request body too large")
    return body


async def verify_p2p_request_auth(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> P2PNetworkConfig:
    """Verify request-bound P2P auth and return network config."""
    config = require_p2p_network(db)

    if LEGACY_PSK_HEADER in request.headers:
        raise HTTPException(
            status_code=403,
            detail=(
                "Static PSK bearer authentication is no longer accepted; "
                "use request-bound HMAC signatures"
            ),
        )

    psk = require_p2p_psk(config)
    body = await _read_bounded_body(request)
    if not verify_request(psk, request.method, request.url.path, request.headers, body):
        raise HTTPException(status_code=403, detail="Invalid P2P request signature")

    return config
