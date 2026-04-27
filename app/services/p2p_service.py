"""P2P network service for File Fridge private sharing."""

from __future__ import annotations

import hashlib
import logging
import socket
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.models import (
    FileInventory,
    FileStatus,
    P2PNetworkConfig,
    P2PPeer,
    P2PPeerStatus,
    RemoteSharedFileCache,
    StorageType,
)

logger = logging.getLogger(__name__)


class P2PService:
    """Manage private-network configuration, peers, and manifest sync."""

    def __init__(self) -> None:
        self._running = False
        try:
            import libp2p  # noqa: F401

            self._backend = "py-libp2p"
        except Exception:
            # We keep an HTTP bridge fallback so deployments missing libp2p still boot.
            self._backend = "http-bridge"

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def running(self) -> bool:
        return self._running

    @staticmethod
    def hash_psk(psk: str) -> str:
        return hashlib.sha256(psk.encode("utf-8")).hexdigest()

    def start_node(self) -> None:
        """Start runtime for P2P features."""
        self._running = True

    def stop_node(self) -> None:
        """Stop runtime for P2P features."""
        self._running = False

    def get_network_config(self, db: Session) -> Optional[P2PNetworkConfig]:
        return db.query(P2PNetworkConfig).order_by(P2PNetworkConfig.id.asc()).first()

    def upsert_network_config(
        self,
        db: Session,
        *,
        network_name: str,
        listen_host: str,
        listen_port: int,
        enabled: bool,
        psk: Optional[str] = None,
    ) -> P2PNetworkConfig:
        config = self.get_network_config(db)
        if config is None:
            if not psk:
                raise ValueError("A PSK is required to create the network configuration")
            config = P2PNetworkConfig(
                network_name=network_name,
                listen_host=listen_host,
                listen_port=listen_port,
                enabled=enabled,
                psk_hash=self.hash_psk(psk),
            )
            db.add(config)
        else:
            config.network_name = network_name
            config.listen_host = listen_host
            config.listen_port = listen_port
            config.enabled = enabled
            if psk:
                config.psk_hash = self.hash_psk(psk)

        db.commit()
        db.refresh(config)
        return config

    def rotate_psk(self, db: Session, psk: str) -> P2PNetworkConfig:
        config = self.get_network_config(db)
        if config is None:
            raise ValueError("P2P network is not configured")

        new_hash = self.hash_psk(psk)
        config.psk_hash = new_hash

        # Hard cutover: all existing peers and remote cache are invalidated.
        db.query(RemoteSharedFileCache).delete()
        db.query(P2PPeer).delete()
        db.commit()
        db.refresh(config)
        return config

    def _validate_join_psk(self, db: Session, join_psk: str) -> P2PNetworkConfig:
        config = self.get_network_config(db)
        if not config:
            raise ValueError("P2P network is not configured")
        if not config.enabled:
            raise ValueError("P2P network is disabled")

        join_hash = self.hash_psk(join_psk)
        if join_hash != config.psk_hash:
            raise ValueError("PSK mismatch; cannot join private network")
        return config

    @staticmethod
    def _check_peer_reachable(host: str, port: int, timeout_s: float = 3.0) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout_s)
        try:
            sock.connect((host, port))
        finally:
            sock.close()

    def join_peer(
        self,
        db: Session,
        *,
        host: str,
        port: int,
        psk: str,
        peer_name: Optional[str],
    ) -> P2PPeer:
        config = self._validate_join_psk(db, psk)

        try:
            self._check_peer_reachable(host, port)
            status = P2PPeerStatus.CONNECTED
            now = datetime.now(timezone.utc)
        except OSError:
            status = P2PPeerStatus.DEGRADED
            now = None

        peer_id = f"{host}:{port}"
        peer = db.query(P2PPeer).filter(P2PPeer.peer_id == peer_id).first()
        if not peer:
            peer = P2PPeer(
                peer_name=peer_name or peer_id,
                peer_id=peer_id,
                host=host,
                port=port,
                psk_hash=config.psk_hash,
                status=status,
                last_seen_at=now,
            )
            db.add(peer)
        else:
            peer.peer_name = peer_name or peer.peer_name
            peer.host = host
            peer.port = port
            peer.psk_hash = config.psk_hash
            peer.status = status
            peer.last_seen_at = now

        db.commit()
        db.refresh(peer)
        return peer

    def list_peers(self, db: Session) -> list[P2PPeer]:
        return db.query(P2PPeer).order_by(P2PPeer.peer_name.asc(), P2PPeer.id.asc()).all()

    def generate_manifest(self, db: Session, instance_name: str) -> dict[str, Any]:
        files = (
            db.query(FileInventory)
            .filter(FileInventory.status == FileStatus.ACTIVE, FileInventory.is_shareable.is_(True))
            .all()
        )

        payload_files: list[dict[str, Any]] = []
        for f in files:
            remote_file_id = f"local:{f.id}"
            payload_files.append(
                {
                    "remote_file_id": remote_file_id,
                    "path_id": f.path_id,
                    "file_path": f.file_path,
                    "display_file_path": f.file_path,
                    "relative_path": None,
                    "storage_type": f.storage_type.value,
                    "file_size": f.file_size,
                    "file_mtime": f.file_mtime.isoformat() if f.file_mtime else None,
                    "checksum": f.checksum,
                    "mime_type": f.mime_type,
                    "file_extension": f.file_extension,
                    "path_name": f.path.name if f.path else None,
                }
            )

        return {
            "peer_name": instance_name,
            "peer_id": str(uuid.uuid4()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "files": payload_files,
            "backend": self.backend,
        }

    def replace_peer_manifest(
        self,
        db: Session,
        *,
        peer: P2PPeer,
        peer_name: str,
        files: list[dict[str, Any]],
    ) -> None:
        peer.peer_name = peer_name or peer.peer_name
        peer.status = P2PPeerStatus.CONNECTED
        peer.last_seen_at = datetime.now(timezone.utc)

        db.query(RemoteSharedFileCache).filter(RemoteSharedFileCache.peer_id == peer.id).delete()

        for item in files:
            mtime_raw = item.get("file_mtime")
            mtime = None
            if mtime_raw:
                try:
                    mtime = datetime.fromisoformat(mtime_raw)
                except ValueError:
                    mtime = None

            storage_type_raw = str(item.get("storage_type") or "hot").lower()
            try:
                storage_type = StorageType(storage_type_raw)
            except ValueError:
                storage_type = StorageType.HOT

            record = RemoteSharedFileCache(
                peer_id=peer.id,
                remote_file_id=item.get("remote_file_id") or str(uuid.uuid4()),
                path_id=item.get("path_id"),
                file_path=item.get("file_path") or "",
                display_file_path=item.get("display_file_path") or item.get("file_path") or "",
                relative_path=item.get("relative_path"),
                storage_type=storage_type,
                file_size=int(item.get("file_size") or 0),
                file_mtime=mtime,
                checksum=item.get("checksum"),
                mime_type=item.get("mime_type"),
                file_extension=item.get("file_extension"),
                path_name=item.get("path_name"),
                last_announced_at=datetime.now(timezone.utc),
            )
            db.add(record)

        db.commit()

    def sync_peer_manifest(
        self,
        db: Session,
        *,
        peer: P2PPeer,
        psk_hash: str,
    ) -> None:
        url = f"http://{peer.host}:{peer.port}/api/v1/p2p/manifest"
        headers = {
            "X-FF-PSK": psk_hash,
            "X-FF-PEER-ID": peer.peer_id,
        }

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                payload = response.json()

            files = payload.get("files") or []
            self.replace_peer_manifest(
                db,
                peer=peer,
                peer_name=payload.get("peer_name") or peer.peer_name,
                files=files,
            )
        except Exception:
            logger.exception("P2P manifest sync failed for peer %s", peer.peer_id)
            peer.status = P2PPeerStatus.DEGRADED
            db.commit()

    def sync_all_peer_manifests(self, db: Session) -> int:
        config = self.get_network_config(db)
        if not config or not config.enabled:
            return 0

        peers = self.list_peers(db)
        synced = 0
        for peer in peers:
            self.sync_peer_manifest(db, peer=peer, psk_hash=config.psk_hash)
            synced += 1
        return synced


p2p_service = P2PService()
