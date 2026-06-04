"""P2P API tests."""

import json

from app.models import P2PPeer, P2PPeerStatus, RemoteSharedFileCache, StorageType
from app.services.p2p_service import p2p_service


def _parse_ndjson(text: str):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_p2p_network_create_and_get(authenticated_client):
    missing = authenticated_client.get("/api/v1/p2p/network")
    assert missing.status_code == 404

    created = authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Test Mesh",
            "listen_host": "0.0.0.0",
            "listen_port": 9119,
            "enabled": True,
            "psk": "super-secret-psk",
        },
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["network_name"] == "Test Mesh"
    assert payload.get("setup_psk") is None

    fetched = authenticated_client.get("/api/v1/p2p/network")
    assert fetched.status_code == 200
    assert fetched.json()["network_name"] == "Test Mesh"


def test_join_peer_requires_matching_psk(authenticated_client):
    authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Main",
            "listen_host": "0.0.0.0",
            "listen_port": 9119,
            "enabled": True,
            "psk": "secret-a",
        },
    )

    rejected = authenticated_client.post(
        "/api/v1/p2p/peers/join",
        json={"host": "127.0.0.1", "port": 9119, "psk": "secret-b", "peer_name": "B"},
    )
    assert rejected.status_code == 400
    assert "PSK mismatch" in rejected.json()["detail"]


def test_create_network_without_psk_auto_generates_psk(authenticated_client):
    created = authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Auto Mesh",
            "listen_host": "0.0.0.0",
            "listen_port": 9119,
            "enabled": True,
        },
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["network_name"] == "Auto Mesh"
    assert isinstance(payload.get("setup_psk"), str)
    assert len(payload["setup_psk"]) >= 16


def test_join_peer_creates_record(authenticated_client, monkeypatch):
    def _sync_peer_manifest(db, *, peer, psk_hash, local_host=None, local_port=None):
        peer.status = P2PPeerStatus.CONNECTED
        db.commit()

    monkeypatch.setattr(p2p_service, "sync_peer_manifest", _sync_peer_manifest)

    authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Main",
            "listen_host": "0.0.0.0",
            "listen_port": 9119,
            "enabled": True,
            "psk": "same-secret",
        },
    )

    response = authenticated_client.post(
        "/api/v1/p2p/peers/join",
        json={"host": "127.0.0.1", "port": 1, "psk": "same-secret", "peer_name": "Site B"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["peer_name"] == "Site B"
    assert payload["peer_id"] == "127.0.0.1:1"


def test_join_peer_bootstraps_local_network_config_when_missing(authenticated_client, monkeypatch):
    def _sync_peer_manifest(db, *, peer, psk_hash, local_host=None, local_port=None):
        peer.status = P2PPeerStatus.CONNECTED
        db.commit()

    monkeypatch.setattr(p2p_service, "sync_peer_manifest", _sync_peer_manifest)

    response = authenticated_client.post(
        "/api/v1/p2p/peers/join",
        json={"host": "127.0.0.1", "port": 1, "psk": "bootstrap-secret", "peer_name": "Site B"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["peer_id"] == "127.0.0.1:1"

    network = authenticated_client.get("/api/v1/p2p/network")
    assert network.status_code == 200
    assert network.json()["network_name"] == "File Fridge P2P"


def test_join_peer_unreachable_returns_400_and_does_not_persist(authenticated_client, db_session):
    authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Main",
            "listen_host": "0.0.0.0",
            "listen_port": 9119,
            "enabled": True,
            "psk": "same-secret",
        },
    )

    response = authenticated_client.post(
        "/api/v1/p2p/peers/join",
        json={"host": "127.0.0.1", "port": 1, "psk": "same-secret", "peer_name": "Site B"},
    )
    assert response.status_code == 400
    assert "Could not reach peer manifest" in response.json()["detail"]
    assert db_session.query(P2PPeer).count() == 0


def test_destroy_network_clears_config_and_state(authenticated_client, db_session):
    authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Main",
            "listen_host": "0.0.0.0",
            "listen_port": 9119,
            "enabled": True,
            "psk": "destroy-secret",
        },
    )
    peer = P2PPeer(
        peer_name="Site B",
        peer_id="peer-destroy-1",
        host="10.10.10.10",
        port=9119,
        status=P2PPeerStatus.CONNECTED,
        psk_hash="x" * 64,
    )
    db_session.add(peer)
    db_session.flush()
    db_session.add(
        RemoteSharedFileCache(
            peer_id=peer.id,
            remote_file_id="remote-destroy-1",
            file_path="/remote/destroy.bin",
            display_file_path="/remote/destroy.bin",
            relative_path="destroy.bin",
            storage_type=StorageType.HOT,
            file_size=10,
        )
    )
    db_session.commit()

    response = authenticated_client.delete("/api/v1/p2p/network")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "destroyed"
    assert payload["removed_networks"] == 1
    assert payload["removed_peers"] >= 1
    assert payload["removed_remote_files"] >= 1

    network = authenticated_client.get("/api/v1/p2p/network")
    assert network.status_code == 404
    peers = authenticated_client.get("/api/v1/p2p/peers")
    assert peers.status_code == 200
    assert peers.json() == []


def test_unjoin_network_clears_peers_and_removes_local_network(
    authenticated_client, db_session
):
    authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Main",
            "listen_host": "0.0.0.0",
            "listen_port": 9119,
            "enabled": True,
            "psk": "unjoin-secret",
        },
    )
    peer = P2PPeer(
        peer_name="Site C",
        peer_id="peer-unjoin-1",
        host="10.10.10.11",
        port=9119,
        status=P2PPeerStatus.CONNECTED,
        psk_hash="y" * 64,
    )
    db_session.add(peer)
    db_session.flush()
    db_session.add(
        RemoteSharedFileCache(
            peer_id=peer.id,
            remote_file_id="remote-unjoin-1",
            file_path="/remote/unjoin.bin",
            display_file_path="/remote/unjoin.bin",
            relative_path="unjoin.bin",
            storage_type=StorageType.COLD,
            file_size=20,
        )
    )
    db_session.commit()

    response = authenticated_client.delete("/api/v1/p2p/peers")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unjoined"
    assert payload["removed_peers"] >= 1
    assert payload["removed_remote_files"] >= 1
    assert payload["removed_networks"] == 1

    network = authenticated_client.get("/api/v1/p2p/network")
    assert network.status_code == 404
    peers = authenticated_client.get("/api/v1/p2p/peers")
    assert peers.status_code == 200
    assert peers.json() == []


def test_p2p_stats_endpoint(authenticated_client):
    empty_stats = authenticated_client.get("/api/v1/p2p/stats")
    assert empty_stats.status_code == 200
    assert empty_stats.json()["health"] == "UNCONFIGURED"
    assert empty_stats.json()["total_peers"] == 0
    assert empty_stats.json()["connected_peers"] == 0

    authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Stats Mesh",
            "listen_host": "0.0.0.0",
            "listen_port": 9119,
            "enabled": True,
            "psk": "stats-secret",
        },
    )
    stats = authenticated_client.get("/api/v1/p2p/stats")
    assert stats.status_code == 200
    payload = stats.json()
    assert payload["network_configured"] is True
    assert payload["health"] in {"IDLE", "HEALTHY", "DEGRADED"}
    assert payload["total_peers"] == 1
    assert payload["connected_peers"] == 1


def test_regenerate_psk_returns_new_psk_and_clears_peers(authenticated_client, db_session):
    authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Main",
            "listen_host": "0.0.0.0",
            "listen_port": 9119,
            "enabled": True,
            "psk": "old-secret",
        },
    )
    peer = P2PPeer(
        peer_name="Site D",
        peer_id="peer-regen-1",
        host="10.10.10.12",
        port=9119,
        status=P2PPeerStatus.CONNECTED,
        psk_hash="z" * 64,
    )
    db_session.add(peer)
    db_session.commit()

    response = authenticated_client.post("/api/v1/p2p/network/psk/regenerate")
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["psk"], str)
    assert len(payload["psk"]) >= 16
    assert "Site D" in payload["offline_peers"]

    peers = authenticated_client.get("/api/v1/p2p/peers")
    assert peers.status_code == 200
    assert peers.json() == []


def test_manifest_requires_psk_header(authenticated_client):
    authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Main",
            "listen_host": "0.0.0.0",
            "listen_port": 9119,
            "enabled": True,
            "psk": "manifest-secret",
        },
    )
    psk_hash = p2p_service.hash_psk("manifest-secret")

    denied = authenticated_client.get("/api/v1/p2p/manifest", headers={"X-FF-PSK": "wrong"})
    assert denied.status_code == 403

    ok = authenticated_client.get(
        "/api/v1/p2p/manifest",
        headers={"X-FF-PSK": psk_hash, "X-FF-PEER-ID": "peer-1"},
    )
    assert ok.status_code == 200
    assert "files" in ok.json()


def test_manifest_push_registers_peer_and_caches_files(authenticated_client, db_session):
    authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Push Mesh",
            "listen_host": "127.0.0.1",
            "listen_port": 8000,
            "enabled": True,
            "psk": "push-secret",
        },
    )
    psk_hash = p2p_service.hash_psk("push-secret")

    response = authenticated_client.post(
        "/api/v1/p2p/manifest/push",
        headers={"X-FF-PSK": psk_hash},
        json={
            "host": "127.0.0.1",
            "port": 8001,
            "peer_name": "Peer B",
            "files": [
                {
                    "remote_file_id": "remote-1",
                    "path_id": 1,
                    "file_path": "/tmp/p2p-a.bin",
                    "display_file_path": "/tmp/p2p-a.bin",
                    "storage_type": "hot",
                    "file_size": 42,
                    "checksum": "abc123",
                }
            ],
        },
    )
    assert response.status_code == 200

    peer = db_session.query(P2PPeer).filter(P2PPeer.peer_id == "127.0.0.1:8001").first()
    assert peer is not None
    assert peer.peer_name == "Peer B"
    assert peer.status == P2PPeerStatus.CONNECTED

    cached = (
        db_session.query(RemoteSharedFileCache)
        .filter(RemoteSharedFileCache.peer_id == peer.id)
        .all()
    )
    assert len(cached) == 1
    assert cached[0].checksum == "abc123"


def test_files_list_includes_remote_cached_rows(authenticated_client, db_session, monitored_path_factory):
    monitored_path = monitored_path_factory(name="Remote Path", source_path="/tmp/remote-path")

    peer = P2PPeer(
        peer_name="Site B",
        peer_id="peer-site-b",
        host="10.0.0.2",
        port=9119,
        status=P2PPeerStatus.CONNECTED,
        psk_hash="x" * 64,
    )
    db_session.add(peer)
    db_session.flush()

    remote_row = RemoteSharedFileCache(
        peer_id=peer.id,
        remote_file_id="remote-1",
        path_id=monitored_path.id,
        file_path="/remote/data/movie.mkv",
        display_file_path="/remote/data/movie.mkv",
        relative_path="movie.mkv",
        storage_type=StorageType.COLD,
        file_size=1234,
        checksum="abc123",
        file_extension=".mkv",
        path_name="Remote Path",
    )
    db_session.add(remote_row)
    db_session.commit()

    response = authenticated_client.get("/api/v1/files")
    assert response.status_code == 200

    messages = _parse_ndjson(response.text)
    file_messages = [m for m in messages if m.get("type") == "file"]
    assert any(m["data"].get("is_remote") is True for m in file_messages)


def test_export_psk_endpoint(authenticated_client):
    # Try fetching when network is not configured yet
    response = authenticated_client.post(
        "/api/v1/p2p/network/psk",
        json={"password": "password"},
    )
    assert response.status_code == 404

    # Setup P2P network
    authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Test Network",
            "listen_host": "0.0.0.0",
            "listen_port": 9119,
            "enabled": True,
            "psk": "my-secret-key-12345",
        },
    )

    # Fetch with incorrect password
    response = authenticated_client.post(
        "/api/v1/p2p/network/psk",
        json={"password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid password"

    # Fetch with correct password
    response = authenticated_client.post(
        "/api/v1/p2p/network/psk",
        json={"password": "password"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["psk"] == "my-secret-key-12345"
    assert payload["available"] is True

