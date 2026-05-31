"""API routes for File Fridge P2P private sharing network."""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants import RESOURCE_REMOTE_CONNECTIONS
from app.database import get_db
from app.models import FileInventory, FileStatus, P2PPeer, P2PPeerStatus, RemoteSharedFileCache
from app.schemas import (
    P2PCurrentPskResponse,
    P2PManifestPushRequest,
    P2PNetworkConfigCreate,
    P2PNetworkConfigResponse,
    P2PNetworkConfigSetupResponse,
    P2PNetworkConfigUpdate,
    P2PNetworkStatsResponse,
    P2PPeerJoinRequest,
    P2PPeerResponse,
    P2PPskAcceptRequest,
    P2PPskRotationResponse,
    P2PPullRequest,
    P2PPullResponse,
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


@router.post("/network", response_model=P2PNetworkConfigSetupResponse)
def create_or_replace_network_config(
    payload: P2PNetworkConfigCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    _ = current_user
    existing = p2p_service.get_network_config(db)
    auto_generated_psk = None
    setup_psk = payload.psk
    if not setup_psk and not existing:
        auto_generated_psk = p2p_service.generate_psk()
        setup_psk = auto_generated_psk

    config = p2p_service.upsert_network_config(
        db,
        network_name=payload.network_name,
        listen_host=payload.listen_host,
        listen_port=payload.listen_port,
        enabled=payload.enabled,
        psk=setup_psk,
    )
    response = P2PNetworkConfigSetupResponse.model_validate(config, from_attributes=True)
    if auto_generated_psk:
        response.setup_psk = auto_generated_psk
    return response


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


@router.delete("/network")
def destroy_network_config(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    _ = current_user
    try:
        removed = p2p_service.destroy_network(db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "destroyed", **removed}


@router.get("/network/psk", response_model=P2PCurrentPskResponse)
def get_network_psk(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    _ = current_user
    config = p2p_service.get_network_config(db)
    if not config:
        raise HTTPException(status_code=404, detail="P2P network is not configured")
    psk = config.get_psk()
    return P2PCurrentPskResponse(psk=psk, available=psk is not None)


@router.post("/network/psk/regenerate", response_model=P2PPskRotationResponse)
def regenerate_network_psk(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    _ = current_user
    current = p2p_service.get_network_config(db)
    if not current:
        raise HTTPException(status_code=404, detail="P2P network is not configured")

    new_psk = p2p_service.generate_psk()
    try:
        result = p2p_service.rotate_psk_with_push(db, new_psk)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return P2PPskRotationResponse(**result)


@router.post("/network/psk/accept")
def accept_psk_rotation(
    payload: P2PPskAcceptRequest,
    db: Annotated[Session, Depends(get_db)],
    x_ff_psk: Annotated[str, Header(alias="X-FF-PSK")],
):
    """Receive a coordinated PSK rotation from a peer node."""
    config = p2p_service.get_network_config(db)
    if not config or not config.enabled:
        raise HTTPException(status_code=503, detail="P2P network is disabled")
    if x_ff_psk != config.psk_hash:
        raise HTTPException(status_code=403, detail="Invalid PSK")

    try:
        new_psk = p2p_service.decrypt_new_psk(payload.encrypted_new_psk, config.psk_hash)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Failed to decrypt PSK payload") from exc

    p2p_service.rotate_psk(db, new_psk)
    return {"status": "accepted"}


@router.get("/peers", response_model=list[P2PPeerResponse])
def list_peers(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    _ = current_user
    peers = p2p_service.list_peers(db)
    counts = dict(
        db.query(
            RemoteSharedFileCache.peer_id,
            func.count(RemoteSharedFileCache.id),
        )
        .group_by(RemoteSharedFileCache.peer_id)
        .all()
    )
    result = []
    for peer in peers:
        peer_dict = {
            "id": peer.id,
            "peer_name": peer.peer_name,
            "peer_id": peer.peer_id,
            "host": peer.host,
            "port": peer.port,
            "status": peer.status,
            "last_seen_at": peer.last_seen_at,
            "created_at": peer.created_at,
            "updated_at": peer.updated_at,
            "file_count": counts.get(peer.id, 0),
        }
        result.append(P2PPeerResponse(**peer_dict))
    return result


@router.post("/peers/join", response_model=P2PPeerResponse)
def join_peer(
    payload: P2PPeerJoinRequest,
    db: Annotated[Session, Depends(get_db)],
    request: Request,
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
        p2p_service.sync_peer_manifest(
            db,
            peer=peer,
            psk_hash=config.psk_hash,
            local_host=request.url.hostname,
            local_port=request.url.port,
        )
        db.refresh(peer)
        if peer.status != P2PPeerStatus.CONNECTED:
            endpoint = f"http://{peer.host}:{peer.port}/api/v1/p2p/manifest"
            db.delete(peer)
            db.commit()
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Could not reach peer manifest at {endpoint}. "
                    "Use the peer File Fridge API port/address and ensure both nodes use the same PSK."
                ),
            )
        # Best-effort reciprocal registration so the target peer can also discover this node.
        local_host = request.url.hostname
        local_port = request.url.port
        if local_host and local_port:
            local_peer_name = instance_config_service.get_instance_name(db) or "File Fridge"
            try:
                p2p_service.push_local_manifest_to_peer(
                    db,
                    peer=peer,
                    psk_hash=config.psk_hash,
                    local_host=local_host,
                    local_port=local_port,
                    local_peer_name=local_peer_name,
                )
            except Exception:
                # Do not fail join when callback registration is unavailable.
                pass
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


@router.delete("/peers")
def unjoin_network(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    _ = current_user
    removed = p2p_service.unjoin_all_peers(db)
    return {"status": "unjoined", **removed}


@router.post("/sync")
def sync_peers(
    db: Annotated[Session, Depends(get_db)],
    request: Request,
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    _ = current_user
    synced = p2p_service.sync_all_peer_manifests(
        db,
        local_host=request.url.hostname,
        local_port=request.url.port,
    )
    return {"synced": synced}


@router.get("/stats", response_model=P2PNetworkStatsResponse)
def get_network_stats(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    _ = current_user
    return p2p_service.get_network_stats(db)


@router.get("/remote-files", response_model=list[P2PRemoteFileCacheResponse])
def list_remote_cached_files(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    _ = current_user
    return (
        db.query(RemoteSharedFileCache).order_by(RemoteSharedFileCache.id.desc()).limit(5000).all()
    )


@router.get("/manifest")
def get_manifest_for_peers(
    db: Annotated[Session, Depends(get_db)],
    x_ff_psk: Annotated[str, Header(alias="X-FF-PSK")],
    x_ff_peer_id: Annotated[str | None, Header(alias="X-FF-PEER-ID")] = None,
    x_ff_local_host: Annotated[str | None, Header(alias="X-FF-LOCAL-HOST")] = None,
    x_ff_local_port: Annotated[str | None, Header(alias="X-FF-LOCAL-PORT")] = None,
):
    config = p2p_service.get_network_config(db)
    if not config or not config.enabled:
        raise HTTPException(status_code=503, detail="P2P network is disabled")
    if x_ff_psk != config.psk_hash:
        raise HTTPException(status_code=403, detail="Invalid PSK")

    # Auto-register the caller as a peer when they advertise their address.
    # This makes discovery self-healing: any successful manifest fetch also registers the caller.
    if x_ff_local_host and x_ff_local_port:
        try:
            port_int = int(x_ff_local_port)
            if 1 <= port_int <= 65535:
                p2p_service.upsert_peer_from_manifest(
                    db,
                    host=x_ff_local_host,
                    port=port_int,
                    psk_hash=config.psk_hash,
                    peer_name=None,
                )
        except (ValueError, TypeError):
            pass
    elif x_ff_peer_id:
        peer = db.query(P2PPeer).filter(P2PPeer.peer_id == x_ff_peer_id).first()
        if peer:
            from datetime import datetime, timezone

            peer.last_seen_at = datetime.now(timezone.utc)
            db.commit()

    instance_name = instance_config_service.get_instance_name(db) or "File Fridge"
    return p2p_service.generate_manifest(db, instance_name=instance_name)


@router.post("/manifest/push")
def push_manifest_from_peer(
    payload: P2PManifestPushRequest,
    db: Annotated[Session, Depends(get_db)],
    x_ff_psk: Annotated[str, Header(alias="X-FF-PSK")],
):
    config = p2p_service.get_network_config(db)
    if not config or not config.enabled:
        raise HTTPException(status_code=503, detail="P2P network is disabled")
    if x_ff_psk != config.psk_hash:
        raise HTTPException(status_code=403, detail="Invalid PSK")

    peer = p2p_service.upsert_peer_from_manifest(
        db,
        host=payload.host,
        port=payload.port,
        psk_hash=config.psk_hash,
        peer_name=payload.peer_name,
    )
    p2p_service.replace_peer_manifest(
        db,
        peer=peer,
        peer_name=payload.peer_name or peer.peer_name,
        files=payload.files or [],
    )
    return {"status": "ok", "peer_id": peer.id}


@router.get("/files/{remote_file_id}/content")
def get_peer_file_content(
    remote_file_id: str,
    db: Annotated[Session, Depends(get_db)],
    x_ff_psk: Annotated[str, Header(alias="X-FF-PSK")],
):
    """Serve a shared file's content to an authenticated peer."""
    config = p2p_service.get_network_config(db)
    if not config or not config.enabled:
        raise HTTPException(status_code=503, detail="P2P network is disabled")
    if x_ff_psk != config.psk_hash:
        raise HTTPException(status_code=403, detail="Invalid PSK")

    if not remote_file_id.startswith("local:"):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        file_id = int(remote_file_id[6:])
    except ValueError:
        raise HTTPException(status_code=404, detail="File not found")

    file = (
        db.query(FileInventory)
        .filter(
            FileInventory.id == file_id,
            FileInventory.status == FileStatus.ACTIVE,
            FileInventory.is_shareable.is_(True),
        )
        .first()
    )
    if not file or not os.path.isfile(file.file_path):
        raise HTTPException(status_code=404, detail="File not found or not available")

    return FileResponse(
        file.file_path,
        filename=os.path.basename(file.file_path),
        media_type="application/octet-stream",
    )


@router.post("/pull", response_model=P2PPullResponse)
def pull_p2p_files(
    payload: P2PPullRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict, Depends(PermissionChecker(RESOURCE_REMOTE_CONNECTIONS))],
):
    """Pull one or more cached remote P2P files into a local monitored path."""
    _ = current_user
    config = p2p_service.get_network_config(db)
    if not config:
        raise HTTPException(status_code=404, detail="P2P network is not configured")

    try:
        result = p2p_service.pull_files(
            db,
            remote_file_cache_ids=payload.remote_file_cache_ids,
            local_path_id=payload.local_path_id,
            psk_hash=config.psk_hash,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return result
