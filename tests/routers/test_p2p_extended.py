"""Extended P2P router tests."""

from app.models import P2PPeer, P2PPeerStatus, RemoteSharedFileCache, StorageType
from app.services.p2p_service import p2p_service


def test_patch_network_rotates_psk(authenticated_client, db_session):
    authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Rotate Mesh",
            "listen_host": "0.0.0.0",
            "listen_port": 9119,
            "enabled": True,
            "psk": "before-rotate",
        },
    )
    peer = P2PPeer(
        peer_name="Peer Rotate",
        peer_id="peer-rotate",
        host="10.0.0.9",
        port=9119,
        status=P2PPeerStatus.CONNECTED,
        psk_hash=p2p_service.hash_psk("before-rotate"),
    )
    db_session.add(peer)
    db_session.commit()

    response = authenticated_client.patch(
        "/api/v1/p2p/network",
        json={"psk": "after-rotate"},
    )
    assert response.status_code == 200
    config = p2p_service.get_network_config(db_session)
    assert config.psk_hash == p2p_service.hash_psk("after-rotate")
    assert db_session.query(P2PPeer).count() == 0


def test_sync_peers_endpoint(authenticated_client, monkeypatch):
    authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Sync Mesh",
            "listen_host": "0.0.0.0",
            "listen_port": 9119,
            "enabled": True,
            "psk": "sync-secret",
        },
    )
    monkeypatch.setattr(p2p_service, "sync_all_peer_manifests", lambda *a, **k: 2)
    response = authenticated_client.post("/api/v1/p2p/sync")
    assert response.status_code == 200
    assert response.json()["synced"] == 2


def test_list_remote_cached_files(authenticated_client, db_session):
    peer = P2PPeer(
        peer_name="Remote Peer",
        peer_id="remote-peer-1",
        host="10.0.0.3",
        port=9119,
        status=P2PPeerStatus.CONNECTED,
        psk_hash="x" * 64,
    )
    db_session.add(peer)
    db_session.flush()
    db_session.add(
        RemoteSharedFileCache(
            peer_id=peer.id,
            remote_file_id="remote-x",
            file_path="/remote/x.bin",
            display_file_path="/remote/x.bin",
            relative_path="x.bin",
            storage_type=StorageType.COLD,
            file_size=5,
        )
    )
    db_session.commit()

    response = authenticated_client.get("/api/v1/p2p/remote-files")
    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_delete_peer_endpoint(authenticated_client, db_session):
    peer = P2PPeer(
        peer_name="Delete Me",
        peer_id="delete-peer",
        host="10.0.0.4",
        port=9119,
        status=P2PPeerStatus.CONNECTED,
        psk_hash="y" * 64,
    )
    db_session.add(peer)
    db_session.commit()

    response = authenticated_client.delete(f"/api/v1/p2p/peers/{peer.id}")
    assert response.status_code == 200
    assert db_session.get(P2PPeer, peer.id) is None


def test_manifest_auto_registers_peer_from_headers(authenticated_client, db_session):
    authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Manifest Mesh",
            "listen_host": "127.0.0.1",
            "listen_port": 8000,
            "enabled": True,
            "psk": "manifest-auto",
        },
    )
    psk_hash = p2p_service.hash_psk("manifest-auto")
    response = authenticated_client.get(
        "/api/v1/p2p/manifest",
        headers={
            "X-FF-PSK": psk_hash,
            "X-FF-LOCAL-HOST": "192.168.1.50",
            "X-FF-LOCAL-PORT": "9120",
        },
    )
    assert response.status_code == 200
    peer = db_session.query(P2PPeer).filter(P2PPeer.peer_id == "192.168.1.50:9120").first()
    assert peer is not None


def test_get_peer_file_content_success(
    authenticated_client, db_session, file_inventory_factory, tmp_path
):
    authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Content Mesh",
            "listen_host": "0.0.0.0",
            "listen_port": 9121,
            "enabled": True,
            "psk": "content-secret",
        },
    )
    config = p2p_service.get_network_config(db_session)
    shareable = file_inventory_factory(
        str(tmp_path / "shared.bin"), create_physical_file=True, size=4
    )
    shareable.is_shareable = True
    db_session.commit()

    response = authenticated_client.get(
        f"/api/v1/p2p/files/local:{shareable.id}/content",
        headers={"X-FF-PSK": config.psk_hash},
    )
    assert response.status_code == 200
    assert response.content


def test_get_peer_file_content_rejects_bad_psk(authenticated_client, db_session):
    authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Bad PSK Mesh",
            "listen_host": "0.0.0.0",
            "listen_port": 9122,
            "enabled": True,
            "psk": "good-secret",
        },
    )
    response = authenticated_client.get(
        "/api/v1/p2p/files/local:1/content",
        headers={"X-FF-PSK": "bad-hash"},
    )
    assert response.status_code == 403


def test_get_peer_file_content_invalid_remote_id(authenticated_client, db_session):
    authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Invalid ID Mesh",
            "listen_host": "0.0.0.0",
            "listen_port": 9123,
            "enabled": True,
            "psk": "id-secret",
        },
    )
    config = p2p_service.get_network_config(db_session)
    headers = {"X-FF-PSK": config.psk_hash}
    assert (
        authenticated_client.get(
            "/api/v1/p2p/files/not-local:1/content", headers=headers
        ).status_code
        == 404
    )
    assert (
        authenticated_client.get(
            "/api/v1/p2p/files/local:not-int/content", headers=headers
        ).status_code
        == 404
    )


def test_get_peer_file_content_when_network_disabled(authenticated_client, db_session):
    authenticated_client.post(
        "/api/v1/p2p/network",
        json={
            "network_name": "Disabled Mesh",
            "listen_host": "0.0.0.0",
            "listen_port": 9124,
            "enabled": False,
            "psk": "disabled-secret",
        },
    )
    config = p2p_service.get_network_config(db_session)
    response = authenticated_client.get(
        "/api/v1/p2p/files/local:1/content",
        headers={"X-FF-PSK": config.psk_hash},
    )
    assert response.status_code == 503
