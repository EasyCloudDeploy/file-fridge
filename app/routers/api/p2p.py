"""API routes for File Fridge P2P private sharing network."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.constants import RESOURCE_REMOTE_CONNECTIONS
from app.database import get_db
from app.models import P2PPeer, RemoteSharedFileCache
from app.schemas import (
    P2PNetworkConfigCreate,
    P2PNetworkConfigResponse,
    P2PNetworkConfigUpdate,
    P2PPeerJoinRequest,
    P2PPeerResponse,
    P2PRemoteFileCacheResponse,
)
from app.security import PermissionChecker
from app.services.instance_config_service import instance_config_service
from app.services.p2p_service import p2p_service

router = APIRouter(prefix="/api/v1/p2p", tags=["p2p"])


@router.get("/network", response_model=P2PNetworkConfigResponse)
def get_network_config(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    _ = current_user
    config = p2p_service.get_network_config(db)
    if not config:
        raise HTTPException(status_code=404, detail="P2P network is not configured")
    return config


@router.post("/network", response_model=P2PNetworkConfigResponse)
def create_or_replace_network_config(
    payload: P2PNetworkConfigCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    _ = current_user
    return p2p_service.upsert_network_config(
        db,
        network_name=payload.network_name,
        listen_host=payload.listen_host,
        listen_port=payload.listen_port,
        enabled=payload.enabled,
        psk=payload.psk,
    )


@router.patch("/network", response_model=P2PNetworkConfigResponse)
def update_network_config(
    payload: P2PNetworkConfigUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    _ = current_user
    current = p2p_service.get_network_config(db)
    if not current:
        raise HTTPException(status_code=404, detail="P2P network is not configured")

    if payload.psk:
        # Hard cutover when rotating PSK.
        return p2p_service.rotate_psk(db, payload.psk)

    return p2p_service.upsert_network_config(
        db,
        network_name=payload.network_name or current.network_name,
        listen_host=payload.listen_host or current.listen_host,
        listen_port=payload.listen_port or current.listen_port,
        enabled=current.enabled if payload.enabled is None else payload.enabled,
        psk=None,
    )


@router.get("/peers", response_model=list[P2PPeerResponse])
def list_peers(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    _ = current_user
    return p2p_service.list_peers(db)


@router.post("/peers/join", response_model=P2PPeerResponse)
def join_peer(
    payload: P2PPeerJoinRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    _ = current_user
    try:
        peer = p2p_service.join_peer(
            db,
            host=payload.host,
            port=payload.port,
            psk=payload.psk,
            peer_name=payload.peer_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    config = p2p_service.get_network_config(db)
    if config:
        p2p_service.sync_peer_manifest(db, peer=peer, psk_hash=config.psk_hash)
        db.refresh(peer)
    return peer


@router.delete("/peers/{peer_id}")
def delete_peer(
    peer_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    _ = current_user
    peer = db.query(P2PPeer).filter(P2PPeer.id == peer_id).first()
    if not peer:
        raise HTTPException(status_code=404, detail="Peer not found")
    db.delete(peer)
    db.commit()
    return {"status": "deleted"}


@router.post("/sync")
def sync_peers(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    _ = current_user
    synced = p2p_service.sync_all_peer_manifests(db)
    return {"synced": synced}


@router.get("/remote-files", response_model=list[P2PRemoteFileCacheResponse])
def list_remote_cached_files(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    _ = current_user
    return db.query(RemoteSharedFileCache).order_by(RemoteSharedFileCache.id.desc()).limit(5000).all()


@router.get("/manifest")
def get_manifest_for_peers(
    db: Annotated[Session, Depends(get_db)],
    x_ff_psk: Annotated[str, Header(alias="X-FF-PSK")],
    x_ff_peer_id: Annotated[str | None, Header(alias="X-FF-PEER-ID")] = None,
):
    config = p2p_service.get_network_config(db)
    if not config or not config.enabled:
        raise HTTPException(status_code=503, detail="P2P network is disabled")
    if x_ff_psk != config.psk_hash:
        raise HTTPException(status_code=403, detail="Invalid PSK")

    if x_ff_peer_id:
        peer = db.query(P2PPeer).filter(P2PPeer.peer_id == x_ff_peer_id).first()
        if peer:
            from datetime import datetime, timezone

            peer.last_seen_at = datetime.now(timezone.utc)
            db.commit()

    instance_name = instance_config_service.get_instance_name(db) or "File Fridge"
    return p2p_service.generate_manifest(db, instance_name=instance_name)
