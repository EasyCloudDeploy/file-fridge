"""Unit tests for p2p_service helpers and rotation flows."""

from unittest.mock import MagicMock

import pytest

from app.models import P2PNetworkConfig, P2PPeer, P2PPeerStatus, RemoteSharedFileCache, StorageType
from app.services.p2p_service import P2PService


@pytest.fixture
def p2p():
    return P2PService()


def test_hash_and_encrypt_psk_roundtrip(p2p):
    psk = p2p.generate_psk()
    psk_hash = p2p.hash_psk(psk)
    token = p2p.encrypt_new_psk("new-secret", psk_hash)
    assert p2p.decrypt_new_psk(token, psk_hash) == "new-secret"


def test_upsert_network_requires_psk_on_create(db_session, p2p):
    with pytest.raises(ValueError, match="PSK is required"):
        p2p.upsert_network_config(
            db_session,
            network_name="Mesh",
            listen_host="0.0.0.0",
            listen_port=9119,
            enabled=True,
            psk=None,
        )


def test_rotate_psk_clears_peers_and_cache(db_session, p2p):
    config = p2p.upsert_network_config(
        db_session,
        network_name="Mesh",
        listen_host="0.0.0.0",
        listen_port=9119,
        enabled=True,
        psk="initial-secret",
    )
    peer = P2PPeer(
        peer_name="Peer",
        peer_id="1.2.3.4:9119",
        host="1.2.3.4",
        port=9119,
        status=P2PPeerStatus.CONNECTED,
        psk_hash=config.psk_hash,
    )
    db_session.add(peer)
    db_session.flush()
    db_session.add(
        RemoteSharedFileCache(
            peer_id=peer.id,
            remote_file_id="r1",
            file_path="/remote/a",
            display_file_path="/remote/a",
            relative_path="a",
            storage_type=StorageType.HOT,
            file_size=1,
        )
    )
    db_session.commit()

    updated = p2p.rotate_psk(db_session, "rotated-secret")
    assert updated.psk_hash == p2p.hash_psk("rotated-secret")
    assert db_session.query(P2PPeer).count() == 0
    assert db_session.query(RemoteSharedFileCache).count() == 0


def test_rotate_psk_with_push_marks_unreachable_peers_offline(db_session, p2p, monkeypatch):
    p2p.upsert_network_config(
        db_session,
        network_name="Mesh",
        listen_host="0.0.0.0",
        listen_port=9119,
        enabled=True,
        psk="rotate-push",
    )
    peer = P2PPeer(
        peer_name="Offline Peer",
        peer_id="9.9.9.9:9119",
        host="9.9.9.9",
        port=9119,
        status=P2PPeerStatus.CONNECTED,
        psk_hash=p2p.hash_psk("rotate-push"),
    )
    db_session.add(peer)
    db_session.commit()

    monkeypatch.setattr(p2p, "_push_rotation_to_peer", MagicMock(side_effect=RuntimeError("down")))

    result = p2p.rotate_psk_with_push(db_session, "brand-new-psk")
    assert result["psk"] == "brand-new-psk"
    assert "Offline Peer" in result["offline_peers"]
    assert db_session.query(P2PPeer).count() == 0


def test_join_peer_updates_existing_record(db_session, p2p, monkeypatch):
    p2p.upsert_network_config(
        db_session,
        network_name="Mesh",
        listen_host="0.0.0.0",
        listen_port=9119,
        enabled=True,
        psk="join-secret",
    )
    monkeypatch.setattr(p2p, "_check_peer_reachable", lambda *_a, **_k: None)

    first = p2p.join_peer(db_session, host="10.0.0.5", port=9000, psk="join-secret", peer_name="A")
    second = p2p.join_peer(
        db_session, host="10.0.0.5", port=9000, psk="join-secret", peer_name="Renamed"
    )
    assert first.id == second.id
    assert second.peer_name == "Renamed"


def test_validate_join_psk_disabled_network(db_session, p2p):
    config = P2PNetworkConfig(
        network_name="Disabled",
        listen_host="0.0.0.0",
        listen_port=9119,
        enabled=False,
        psk_hash=p2p.hash_psk("secret"),
    )
    config.set_psk("secret")
    db_session.add(config)
    db_session.commit()

    with pytest.raises(ValueError, match="disabled"):
        p2p._validate_join_psk(db_session, "secret")


def test_start_and_stop_node(p2p):
    assert p2p.running is False
    p2p.start_node()
    assert p2p.running is True
    p2p.stop_node()
    assert p2p.running is False


def test_pull_files_success_and_failures(
    db_session, p2p, monitored_path_factory, tmp_path, monkeypatch
):
    hot = tmp_path / "pull_hot"
    hot.mkdir()
    path = monitored_path_factory("Pull Path", str(hot))

    peer = P2PPeer(
        peer_name="Peer",
        peer_id="1.2.3.4:9001",
        host="1.2.3.4",
        port=9001,
        status=P2PPeerStatus.CONNECTED,
        psk_hash=p2p.hash_psk("psk"),
    )
    db_session.add(peer)
    db_session.flush()
    cache = RemoteSharedFileCache(
        peer_id=peer.id,
        remote_file_id="remote-1",
        file_path="/remote/data.bin",
        display_file_path="/remote/data.bin",
        relative_path="data.bin",
        storage_type=StorageType.HOT,
        file_size=4,
    )
    db_session.add(cache)
    db_session.commit()

    class FakeResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self, chunk_size=65536):
            yield b"data"

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url, headers=None):
            assert "X-FF-PSK" in headers
            return FakeResponse()

    monkeypatch.setattr("app.services.p2p_service.httpx.Client", lambda **kwargs: FakeClient())

    result = p2p.pull_files(
        db_session,
        remote_file_cache_ids=[cache.id, 99999],
        local_path_id=path.id,
        psk_hash="hash",
    )
    assert result["pulled"] == 1
    assert (hot / "data.bin").read_bytes() == b"data"
    assert any(item["id"] == 99999 for item in result["errors"])


def test_pull_files_missing_local_path(db_session, p2p):
    with pytest.raises(ValueError, match="Local path"):
        p2p.pull_files(db_session, remote_file_cache_ids=[], local_path_id=99999, psk_hash="h")


def test_sync_all_peer_manifests_no_config(db_session, p2p):
    assert p2p.sync_all_peer_manifests(db_session) == 0


def test_generate_manifest_and_network_stats(db_session, p2p, monitored_path_factory, tmp_path):
    hot = tmp_path / "manifest_hot"
    hot.mkdir()
    path = monitored_path_factory("Manifest", str(hot))
    (hot / "share.txt").write_text("x")
    p2p.upsert_network_config(
        db_session,
        network_name="Mesh",
        listen_host="0.0.0.0",
        listen_port=9119,
        enabled=True,
        psk="manifest-psk",
    )
    manifest = p2p.generate_manifest(db_session, instance_name="test-instance")
    assert "files" in manifest
    stats = p2p.get_network_stats(db_session)
    assert stats["total_peers"] >= 0


def test_pull_files_existing_destination(db_session, p2p, monitored_path_factory, tmp_path):
    hot = tmp_path / "pull_exists_hot"
    hot.mkdir()
    path = monitored_path_factory("Pull Exists", str(hot))
    (hot / "data.bin").write_bytes(b"exists")
    peer = P2PPeer(
        peer_name="Peer",
        peer_id="2.3.4.5:9001",
        host="2.3.4.5",
        port=9001,
        status=P2PPeerStatus.CONNECTED,
        psk_hash="peer-hash",
    )
    db_session.add(peer)
    db_session.flush()
    cache = RemoteSharedFileCache(
        peer_id=peer.id,
        remote_file_id="remote-2",
        file_path="/remote/data.bin",
        display_file_path="/remote/data.bin",
        relative_path="data.bin",
        storage_type=StorageType.HOT,
        file_size=4,
    )
    db_session.add(cache)
    db_session.commit()
    result = p2p.pull_files(
        db_session,
        remote_file_cache_ids=[cache.id],
        local_path_id=path.id,
        psk_hash="hash",
    )
    assert result["failed"] == 1


def test_pull_files_http_error(db_session, p2p, monitored_path_factory, tmp_path, monkeypatch):
    import httpx

    hot = tmp_path / "pull_http_hot"
    hot.mkdir()
    path = monitored_path_factory("Pull HTTP", str(hot))
    peer = P2PPeer(
        peer_name="Peer",
        peer_id="3.4.5.6:9001",
        host="3.4.5.6",
        port=9001,
        status=P2PPeerStatus.CONNECTED,
        psk_hash="peer-hash",
    )
    db_session.add(peer)
    db_session.flush()
    cache = RemoteSharedFileCache(
        peer_id=peer.id,
        remote_file_id="remote-3",
        file_path="/remote/http.bin",
        display_file_path="/remote/http.bin",
        relative_path="http.bin",
        storage_type=StorageType.HOT,
        file_size=4,
    )
    db_session.add(cache)
    db_session.commit()

    class FakeResponse:
        status_code = 503

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            request = httpx.Request("GET", "http://example")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("fail", request=request, response=response)

        def iter_bytes(self, chunk_size=65536):
            yield b""

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url, headers=None):
            return FakeResponse()

    monkeypatch.setattr("app.services.p2p_service.httpx.Client", lambda **kwargs: FakeClient())
    result = p2p.pull_files(
        db_session,
        remote_file_cache_ids=[cache.id],
        local_path_id=path.id,
        psk_hash="hash",
    )
    assert result["failed"] == 1
    assert "HTTP 503" in result["errors"][0]["error"]


def test_decrypt_new_psk_invalid_token(p2p):
    from cryptography.fernet import InvalidToken

    with pytest.raises(InvalidToken):
        p2p.decrypt_new_psk("not-a-token", p2p.hash_psk("secret"))


def test_join_peer_unreachable_marks_degraded(db_session, p2p, monkeypatch):
    p2p.upsert_network_config(
        db_session,
        network_name="Mesh",
        listen_host="0.0.0.0",
        listen_port=9119,
        enabled=True,
        psk="join-secret",
    )
    monkeypatch.setattr(p2p, "_check_peer_reachable", MagicMock(side_effect=OSError("down")))
    peer = p2p.join_peer(
        db_session, host="10.0.0.8", port=9002, psk="join-secret", peer_name="Degraded"
    )
    assert peer.status == P2PPeerStatus.DEGRADED


def test_upsert_peer_from_manifest_updates_existing(db_session, p2p):
    existing = P2PPeer(
        peer_name="Old Name",
        peer_id="10.0.0.7:9003",
        host="10.0.0.7",
        port=9003,
        status=P2PPeerStatus.CONNECTED,
        psk_hash="hash",
    )
    db_session.add(existing)
    db_session.commit()
    updated = p2p.upsert_peer_from_manifest(
        db_session,
        host="10.0.0.7",
        port=9003,
        psk_hash="new-hash",
        peer_name="New Name",
    )
    assert updated.id == existing.id
    assert updated.peer_name == "New Name"
    assert updated.psk_hash == "new-hash"


def test_validate_join_psk_mismatch(db_session, p2p):
    p2p.upsert_network_config(
        db_session,
        network_name="Mesh",
        listen_host="0.0.0.0",
        listen_port=9119,
        enabled=True,
        psk="correct",
    )
    with pytest.raises(ValueError, match="PSK mismatch"):
        p2p._validate_join_psk(db_session, "wrong")
