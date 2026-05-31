"""P2P network service for File Fridge private sharing."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import socket
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    FileInventory,
    FileStatus,
    MonitoredPath,
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

    @staticmethod
    def generate_psk() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def _rotation_fernet(psk_hash: str) -> Fernet:
        """Return a Fernet instance keyed from the PSK hash for rotation envelope encryption."""
        key = base64.urlsafe_b64encode(
            hashlib.sha256((psk_hash + ":ff-psk-rotation-v1").encode()).digest()
        )
        return Fernet(key)

    @classmethod
    def encrypt_new_psk(cls, new_psk: str, current_psk_hash: str) -> str:
        return cls._rotation_fernet(current_psk_hash).encrypt(new_psk.encode()).decode()

    @classmethod
    def decrypt_new_psk(cls, token: str, current_psk_hash: str) -> str:
        return cls._rotation_fernet(current_psk_hash).decrypt(token.encode()).decode()

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
            config.set_psk(psk)
            db.add(config)
        else:
            config.network_name = network_name
            config.listen_host = listen_host
            config.listen_port = listen_port
            config.enabled = enabled
            if psk:
                config.psk_hash = self.hash_psk(psk)
                config.set_psk(psk)

        db.commit()
        db.refresh(config)
        return config

    def rotate_psk(self, db: Session, psk: str) -> P2PNetworkConfig:
        """Hard-reset rotation: update PSK and clear all peers and remote cache."""
        config = self.get_network_config(db)
        if config is None:
            raise ValueError("P2P network is not configured")

        config.psk_hash = self.hash_psk(psk)
        config.set_psk(psk)

        db.query(RemoteSharedFileCache).delete()
        db.query(P2PPeer).delete()
        db.commit()
        db.refresh(config)
        return config

    def rotate_psk_with_push(self, db: Session, new_psk: str) -> dict[str, Any]:
        """Coordinated rotation: push new PSK to all online peers, then rotate locally.

        Online peers receive the new PSK encrypted with a key derived from the current PSK hash —
        only nodes already in the cluster can decrypt it. Peers that cannot be reached are removed
        and must be reconfigured manually when they come back online.
        """
        config = self.get_network_config(db)
        if config is None:
            raise ValueError("P2P network is not configured")

        current_psk_hash = config.psk_hash
        peers = self.list_peers(db)

        updated: list[str] = []
        offline: list[str] = []

        for peer in peers:
            try:
                self._push_rotation_to_peer(peer, current_psk_hash, new_psk)
                updated.append(peer.peer_name)
            except Exception:
                logger.warning("PSK rotation push failed for peer %s", peer.peer_id)
                offline.append(peer.peer_name)
                db.query(RemoteSharedFileCache).filter(
                    RemoteSharedFileCache.peer_id == peer.id
                ).delete()
                db.delete(peer)

        config.psk_hash = self.hash_psk(new_psk)
        config.set_psk(new_psk)
        db.commit()
        db.refresh(config)

        return {
            "psk": new_psk,
            "updated_peers": updated,
            "offline_peers": offline,
        }

    def _push_rotation_to_peer(self, peer: P2PPeer, current_psk_hash: str, new_psk: str) -> None:
        """Send the encrypted new PSK to a single peer."""
        encrypted = self.encrypt_new_psk(new_psk, current_psk_hash)
        url = f"http://{peer.host}:{peer.port}/api/v1/p2p/network/psk/accept"
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                url,
                headers={"X-FF-PSK": current_psk_hash, "Content-Type": "application/json"},
                json={"encrypted_new_psk": encrypted},
            )
            response.raise_for_status()

    def destroy_network(self, db: Session) -> dict[str, int]:
        """Destroy local P2P network configuration and clear all P2P state."""
        config = self.get_network_config(db)
        if config is None:
            raise ValueError("P2P network is not configured")

        removed_remote_files = db.query(RemoteSharedFileCache).delete()
        removed_peers = db.query(P2PPeer).delete()
        db.delete(config)
        db.commit()
        return {
            "removed_networks": 1,
            "removed_peers": int(removed_peers),
            "removed_remote_files": int(removed_remote_files),
        }

    def unjoin_all_peers(self, db: Session) -> dict[str, int]:
        """Leave the private network completely for this instance."""
        config = self.get_network_config(db)
        removed_remote_files = db.query(RemoteSharedFileCache).delete()
        removed_peers = db.query(P2PPeer).delete()
        removed_networks = 0
        if config is not None:
            db.delete(config)
            removed_networks = 1
        db.commit()
        return {
            "removed_peers": int(removed_peers),
            "removed_remote_files": int(removed_remote_files),
            "removed_networks": int(removed_networks),
        }

    def _validate_join_psk(self, db: Session, join_psk: str) -> P2PNetworkConfig:
        config = self.get_network_config(db)
        join_hash = self.hash_psk(join_psk)
        if not config:
            # Join-first workflow: bootstrap local network config from join payload.
            config = P2PNetworkConfig(
                network_name="File Fridge P2P",
                listen_host=settings.p2p_listen_host,
                listen_port=settings.p2p_listen_port,
                enabled=True,
                psk_hash=join_hash,
            )
            config.set_psk(join_psk)
            db.add(config)
            db.commit()
            db.refresh(config)
            return config
        if not config.enabled:
            raise ValueError("P2P network is disabled")

        if join_hash != config.psk_hash:
            raise ValueError("PSK mismatch; cannot join private network")
        # Opportunistically store plaintext if this node hasn't done so yet.
        if not config.psk_encrypted:
            config.set_psk(join_psk)
            db.commit()
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

    def upsert_peer_from_manifest(
        self,
        db: Session,
        *,
        host: str,
        port: int,
        psk_hash: str,
        peer_name: Optional[str],
    ) -> P2PPeer:
        peer_id = f"{host}:{port}"
        now = datetime.now(timezone.utc)
        peer = db.query(P2PPeer).filter(P2PPeer.peer_id == peer_id).first()
        if not peer:
            peer = P2PPeer(
                peer_name=peer_name or peer_id,
                peer_id=peer_id,
                host=host,
                port=port,
                psk_hash=psk_hash,
                status=P2PPeerStatus.CONNECTED,
                last_seen_at=now,
            )
            db.add(peer)
        else:
            peer.peer_name = peer_name or peer.peer_name
            peer.host = host
            peer.port = port
            peer.psk_hash = psk_hash
            peer.status = P2PPeerStatus.CONNECTED
            peer.last_seen_at = now

        db.commit()
        db.refresh(peer)
        return peer

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
        local_host: Optional[str] = None,
        local_port: Optional[int] = None,
    ) -> None:
        url = f"http://{peer.host}:{peer.port}/api/v1/p2p/manifest"
        headers: dict[str, str] = {"X-FF-PSK": psk_hash}
        # Advertise our own address so the remote can auto-register us as a peer,
        # making discovery self-healing when the initial reciprocal push failed.
        if local_host and local_port:
            headers["X-FF-LOCAL-HOST"] = local_host
            headers["X-FF-LOCAL-PORT"] = str(local_port)

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

    def sync_all_peer_manifests(
        self,
        db: Session,
        *,
        local_host: Optional[str] = None,
        local_port: Optional[int] = None,
    ) -> int:
        config = self.get_network_config(db)
        if not config or not config.enabled:
            return 0

        peers = self.list_peers(db)
        synced = 0
        for peer in peers:
            self.sync_peer_manifest(
                db,
                peer=peer,
                psk_hash=config.psk_hash,
                local_host=local_host,
                local_port=local_port,
            )
            synced += 1
        return synced

    def push_local_manifest_to_peer(
        self,
        db: Session,
        *,
        peer: P2PPeer,
        psk_hash: str,
        local_host: str,
        local_port: int,
        local_peer_name: str,
    ) -> None:
        """Push this node's manifest to a peer so they can register us symmetrically."""
        manifest = self.generate_manifest(db, instance_name=local_peer_name)
        payload = {
            "host": local_host,
            "port": local_port,
            "peer_name": local_peer_name,
            "files": manifest.get("files") or [],
        }
        url = f"http://{peer.host}:{peer.port}/api/v1/p2p/manifest/push"
        headers = {"X-FF-PSK": psk_hash}
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()

    def pull_files(
        self,
        db: Session,
        *,
        remote_file_cache_ids: list[int],
        local_path_id: int,
        psk_hash: str,
    ) -> dict[str, Any]:
        local_path = db.query(MonitoredPath).filter(MonitoredPath.id == local_path_id).first()
        if not local_path:
            raise ValueError(f"Local path {local_path_id} not found")
        if not os.path.isdir(local_path.source_path):
            raise ValueError(f"Local path directory does not exist: {local_path.source_path}")

        pulled: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for cache_id in remote_file_cache_ids:
            cache_entry = (
                db.query(RemoteSharedFileCache).filter(RemoteSharedFileCache.id == cache_id).first()
            )
            if not cache_entry:
                failed.append({"id": cache_id, "error": "Remote file not found in cache"})
                continue

            peer = db.query(P2PPeer).filter(P2PPeer.id == cache_entry.peer_id).first()
            if not peer:
                failed.append({"id": cache_id, "error": "Peer not found"})
                continue

            filename = os.path.basename(cache_entry.file_path)
            if not filename:
                failed.append({"id": cache_id, "error": "Could not determine filename"})
                continue

            dest_path = os.path.join(local_path.source_path, filename)
            if os.path.exists(dest_path):
                failed.append(
                    {"id": cache_id, "error": f"{filename} already exists in the destination path"}
                )
                continue

            encoded_id = quote(cache_entry.remote_file_id, safe="")
            url = f"http://{peer.host}:{peer.port}/api/v1/p2p/files/{encoded_id}/content"
            headers = {"X-FF-PSK": psk_hash}

            try:
                with httpx.Client(timeout=300.0) as client:
                    with client.stream("GET", url, headers=headers) as response:
                        response.raise_for_status()
                        with open(dest_path, "wb") as fh:
                            for chunk in response.iter_bytes(chunk_size=65536):
                                fh.write(chunk)
                pulled.append({"id": cache_id, "dest_path": dest_path, "filename": filename})
            except httpx.HTTPStatusError as exc:
                failed.append(
                    {"id": cache_id, "error": f"Peer returned HTTP {exc.response.status_code}"}
                )
            except Exception as exc:
                failed.append({"id": cache_id, "error": str(exc)})

        return {
            "pulled": len(pulled),
            "failed": len(failed),
            "results": pulled,
            "errors": failed,
        }

    def get_network_stats(self, db: Session) -> dict[str, Any]:
        config = self.get_network_config(db)
        peers = self.list_peers(db)
        remote_total_peers = len(peers)
        remote_connected_peers = len([p for p in peers if p.status == P2PPeerStatus.CONNECTED])
        remote_degraded_peers = len([p for p in peers if p.status == P2PPeerStatus.DEGRADED])

        # Every configured instance participates as a peer in the private network.
        local_peer_present = 1 if config and config.enabled else 0
        local_peer_connected = 1 if local_peer_present and self.running else 0
        local_peer_degraded = 1 if local_peer_present and not self.running else 0

        total_peers = remote_total_peers + local_peer_present
        connected_peers = remote_connected_peers + local_peer_connected
        degraded_peers = remote_degraded_peers + local_peer_degraded
        remote_cached_files = db.query(RemoteSharedFileCache).count()
        local_file_count = (
            db.query(FileInventory)
            .filter(
                FileInventory.status == FileStatus.ACTIVE,
                FileInventory.is_shareable.is_(True),
            )
            .count()
        )
        cluster_file_count = local_file_count + remote_cached_files

        if not config:
            health = "UNCONFIGURED"
        elif remote_total_peers == 0:
            health = "IDLE"
        elif degraded_peers > 0:
            health = "DEGRADED"
        else:
            health = "HEALTHY"

        return {
            "network_configured": bool(config),
            "backend": self.backend,
            "node_running": self.running,
            "total_peers": total_peers,
            "connected_peers": connected_peers,
            "degraded_peers": degraded_peers,
            "remote_cached_files": remote_cached_files,
            "cluster_file_count": cluster_file_count,
            "health": health,
        }


p2p_service = P2PService()
