"""P2P API tests."""

import json

from app.models import P2PPeer, P2PPeerStatus, RemoteSharedFileCache, StorageType


def _parse_ndjson(text: str):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_p2p_network_create_and_get(authenticated_client):
    missing = authenticated_client.get('/api/v1/p2p/network')
    assert missing.status_code == 404

    created = authenticated_client.post(
        '/api/v1/p2p/network',
        json={
            'network_name': 'Test Mesh',
            'listen_host': '0.0.0.0',
            'listen_port': 9119,
            'enabled': True,
            'psk': 'super-secret-psk',
        },
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload['network_name'] == 'Test Mesh'
    assert payload['psk_hash']

    fetched = authenticated_client.get('/api/v1/p2p/network')
    assert fetched.status_code == 200
    assert fetched.json()['network_name'] == 'Test Mesh'


def test_join_peer_requires_matching_psk(authenticated_client):
    authenticated_client.post(
        '/api/v1/p2p/network',
        json={
            'network_name': 'Main',
            'listen_host': '0.0.0.0',
            'listen_port': 9119,
            'enabled': True,
            'psk': 'secret-a',
        },
    )

    rejected = authenticated_client.post(
        '/api/v1/p2p/peers/join',
        json={'host': '127.0.0.1', 'port': 9119, 'psk': 'secret-b', 'peer_name': 'B'},
    )
    assert rejected.status_code == 400
    assert 'PSK mismatch' in rejected.json()['detail']


def test_join_peer_creates_record(authenticated_client):
    authenticated_client.post(
        '/api/v1/p2p/network',
        json={
            'network_name': 'Main',
            'listen_host': '0.0.0.0',
            'listen_port': 9119,
            'enabled': True,
            'psk': 'same-secret',
        },
    )

    response = authenticated_client.post(
        '/api/v1/p2p/peers/join',
        json={'host': '127.0.0.1', 'port': 1, 'psk': 'same-secret', 'peer_name': 'Site B'},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload['peer_name'] == 'Site B'
    assert payload['peer_id'] == '127.0.0.1:1'


def test_manifest_requires_psk_header(authenticated_client):
    authenticated_client.post(
        '/api/v1/p2p/network',
        json={
            'network_name': 'Main',
            'listen_host': '0.0.0.0',
            'listen_port': 9119,
            'enabled': True,
            'psk': 'manifest-secret',
        },
    )
    config = authenticated_client.get('/api/v1/p2p/network').json()

    denied = authenticated_client.get('/api/v1/p2p/manifest', headers={'X-FF-PSK': 'wrong'})
    assert denied.status_code == 403

    ok = authenticated_client.get(
        '/api/v1/p2p/manifest',
        headers={'X-FF-PSK': config['psk_hash'], 'X-FF-PEER-ID': 'peer-1'},
    )
    assert ok.status_code == 200
    assert 'files' in ok.json()


def test_files_list_includes_remote_cached_rows(authenticated_client, db_session, monitored_path_factory):
    monitored_path = monitored_path_factory(name='Remote Path', source_path='/tmp/remote-path')

    peer = P2PPeer(
        peer_name='Site B',
        peer_id='peer-site-b',
        host='10.0.0.2',
        port=9119,
        status=P2PPeerStatus.CONNECTED,
        psk_hash='x' * 64,
    )
    db_session.add(peer)
    db_session.flush()

    remote_row = RemoteSharedFileCache(
        peer_id=peer.id,
        remote_file_id='remote-1',
        path_id=monitored_path.id,
        file_path='/remote/data/movie.mkv',
        display_file_path='/remote/data/movie.mkv',
        relative_path='movie.mkv',
        storage_type=StorageType.COLD,
        file_size=1234,
        checksum='abc123',
        file_extension='.mkv',
        path_name='Remote Path',
    )
    db_session.add(remote_row)
    db_session.commit()

    response = authenticated_client.get('/api/v1/files')
    assert response.status_code == 200

    messages = _parse_ndjson(response.text)
    file_messages = [m for m in messages if m.get('type') == 'file']
    assert any(m['data'].get('is_remote') is True for m in file_messages)
