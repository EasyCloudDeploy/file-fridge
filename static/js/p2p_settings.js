(function () {
    let currentNetworkConfig = null;

    function toastSuccess(message) {
        if (typeof showToast === 'function') {
            showToast('Success', message, 'success');
        }
    }

    function toastError(message) {
        if (typeof showToast === 'function') {
            showToast('Error', message, 'danger');
        }
    }

    function extractErrorMessage(errorData, fallback) {
        const detail = errorData && errorData.detail;
        if (!detail) return fallback;
        if (Array.isArray(detail)) return detail.map(e => e.msg || JSON.stringify(e)).join('; ');
        return String(detail);
    }

    function toLocalDate(value) {
        if (!value) return 'Never';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return 'Never';
        return date.toLocaleString();
    }

    async function loadNetworkConfig() {
        const nameEl = document.getElementById('p2p-network-name');
        const hostEl = document.getElementById('p2p-listen-host');
        const portEl = document.getElementById('p2p-listen-port');
        const pskEl = document.getElementById('p2p-psk');
        if (!nameEl || !hostEl || !portEl || !pskEl) return;

        try {
            const response = await authenticatedFetch('/api/v1/p2p/network');
            if (response.status === 404) {
                currentNetworkConfig = null;
                nameEl.value = 'File Fridge P2P';
                hostEl.value = '0.0.0.0';
                portEl.value = '9119';
                pskEl.value = '';
                return;
            }
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const config = await response.json();
            currentNetworkConfig = config;
            nameEl.value = config.network_name || 'File Fridge P2P';
            hostEl.value = config.listen_host || '0.0.0.0';
            portEl.value = config.listen_port || 9119;
            pskEl.value = '';
        } catch (error) {
            console.error('Failed to load P2P config:', error);
            toastError(`Failed to load P2P config: ${error.message}`);
        }
    }

    async function saveNetworkConfig(event) {
        event.preventDefault();

        const name = document.getElementById('p2p-network-name')?.value?.trim() || 'File Fridge P2P';
        const host = document.getElementById('p2p-listen-host')?.value?.trim() || '0.0.0.0';
        const portRaw = document.getElementById('p2p-listen-port')?.value;
        const psk = document.getElementById('p2p-psk')?.value?.trim() || '';
        const port = parseInt(portRaw || '9119', 10);

        try {
            let response;
            if (!currentNetworkConfig) {
                if (!psk) {
                    throw new Error('PSK is required to create a P2P network');
                }
                response = await authenticatedFetch('/api/v1/p2p/network', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        network_name: name,
                        listen_host: host,
                        listen_port: port,
                        enabled: true,
                        psk
                    })
                });
            } else {
                response = await authenticatedFetch('/api/v1/p2p/network', {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        network_name: name,
                        listen_host: host,
                        listen_port: port,
                        enabled: true,
                        psk: psk || undefined
                    })
                });
            }

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ detail: 'Request failed' }));
                throw new Error(extractErrorMessage(errorData, 'Could not save P2P network'));
            }

            toastSuccess('P2P network configuration saved');
            await loadNetworkConfig();
            await loadPeers();
        } catch (error) {
            console.error('Failed to save P2P config:', error);
            toastError(error.message);
        }
    }

    async function joinPeer(event) {
        event.preventDefault();

        const host = document.getElementById('p2p-peer-host')?.value?.trim();
        const port = parseInt(document.getElementById('p2p-peer-port')?.value || '0', 10);
        const peerName = document.getElementById('p2p-peer-name')?.value?.trim();
        const psk = document.getElementById('p2p-peer-psk')?.value?.trim();

        if (!host || !port || !psk) {
            toastError('Host, port, and PSK are required');
            return;
        }

        try {
            const response = await authenticatedFetch('/api/v1/p2p/peers/join', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ host, port, psk, peer_name: peerName || undefined })
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ detail: 'Request failed' }));
                throw new Error(extractErrorMessage(errorData, 'Failed to join peer'));
            }

            toastSuccess('Peer joined');
            document.getElementById('p2p-join-form')?.reset();
            await loadPeers();
        } catch (error) {
            console.error('Failed to join peer:', error);
            toastError(error.message);
        }
    }

    async function syncPeers() {
        const syncBtn = document.querySelector('#sync-p2p-peers-btn');
        if (syncBtn) syncBtn.disabled = true;
        try {
            const response = await authenticatedFetch('/api/v1/p2p/sync', { method: 'POST' });
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ detail: 'Sync failed' }));
                throw new Error(extractErrorMessage(errorData, 'Sync failed'));
            }
            await loadPeers();
            toastSuccess('Peer manifests synced');
        } catch (error) {
            console.error('Failed to sync peers:', error);
            toastError(error.message);
        } finally {
            if (syncBtn) syncBtn.disabled = false;
        }
    }

    async function loadPeers() {
        const tbody = document.getElementById('p2p-peers-list');
        if (!tbody) return;

        try {
            const response = await authenticatedFetch('/api/v1/p2p/peers');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const peers = await response.json();
            if (!Array.isArray(peers) || peers.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" class="text-muted text-center py-3">No peers joined yet.</td></tr>';
                return;
            }

            tbody.innerHTML = peers.map(peer => {
                const status = (peer.status || 'DISCONNECTED').toUpperCase();
                const badgeClass = status === 'CONNECTED'
                    ? 'bg-success'
                    : (status === 'DEGRADED' ? 'bg-warning text-dark' : 'bg-secondary');
                return `
                    <tr>
                        <td>${escapeHtml(peer.peer_name || peer.peer_id)}</td>
                        <td><code>${escapeHtml(`${peer.host}:${peer.port}`)}</code></td>
                        <td><span class="badge ${badgeClass}">${escapeHtml(status)}</span></td>
                        <td>${escapeHtml(toLocalDate(peer.last_seen_at))}</td>
                    </tr>
                `;
            }).join('');
        } catch (error) {
            console.error('Failed to load peers:', error);
            tbody.innerHTML = '<tr><td colspan="4" class="text-danger text-center py-3">Failed to load peers.</td></tr>';
        }
    }

    function escapeHtml(value) {
        const div = document.createElement('div');
        div.textContent = value || '';
        return div.innerHTML;
    }

    async function initP2PSettings() {
        const networkForm = document.getElementById('p2p-network-form');
        const joinForm = document.getElementById('p2p-join-form');
        const syncBtn = document.getElementById('sync-p2p-peers-btn');

        if (!networkForm || !joinForm) {
            return;
        }

        networkForm.addEventListener('submit', saveNetworkConfig);
        joinForm.addEventListener('submit', joinPeer);
        if (syncBtn) {
            syncBtn.addEventListener('click', syncPeers);
        }

        await loadNetworkConfig();
        await loadPeers();
    }

    if (typeof window.runWhenFileFridgeReady === 'function') {
        window.runWhenFileFridgeReady(() => { void initP2PSettings(); });
    } else {
        void initP2PSettings();
    }
})();
