(function () {
    let currentNetworkConfig = null;

    function toastSuccess(message) {
        if (typeof showToast === 'function') {
            showToast(message, 'success');
        }
    }

    function toastError(message) {
        if (typeof showToast === 'function') {
            showToast(message, 'error');
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

    function escapeHtml(value) {
        const div = document.createElement('div');
        div.textContent = value || '';
        return div.innerHTML;
    }



    function showWizardMode() {
        document.getElementById('p2p-setup-wizard')?.classList.remove('d-none');
        document.getElementById('p2p-management')?.classList.add('d-none');
    }

    function showManagementMode() {
        document.getElementById('p2p-setup-wizard')?.classList.add('d-none');
        document.getElementById('p2p-management')?.classList.remove('d-none');
    }

    function getJoinEndpoint() {
        const host = window.location.hostname || '127.0.0.1';
        const port = window.location.port || (window.location.protocol === 'https:' ? '443' : '80');
        return { host, port };
    }

    function setWizardChoice(mode) {
        const createBtn = document.getElementById('wizard-choice-create');
        const joinBtn = document.getElementById('wizard-choice-join');
        const createPane = document.getElementById('p2p-wizard-create-pane');
        const joinPane = document.getElementById('p2p-wizard-join-pane');

        const isCreate = mode === 'create';
        createBtn?.classList.toggle('active', isCreate);
        joinBtn?.classList.toggle('active', !isCreate);
        createPane?.classList.toggle('d-none', !isCreate);
        joinPane?.classList.toggle('d-none', isCreate);
    }

    async function apiJson(url, options) {
        const response = await authenticatedFetch(url, options);
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Request failed' }));
            throw new Error(extractErrorMessage(errorData, `Request failed (${response.status})`));
        }
        return response.status === 204 ? null : response.json();
    }

    async function refreshNetworkConfig() {
        try {
            const response = await authenticatedFetch('/api/v1/p2p/network');
            if (response.status === 404) {
                currentNetworkConfig = null;
                return null;
            }
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            currentNetworkConfig = await response.json();
            return currentNetworkConfig;
        } catch (error) {
            console.error('Failed to load network config:', error);
            toastError(`Failed to load P2P config: ${error.message}`);
            return null;
        }
    }

    async function loadPeers() {
        const tbody = document.getElementById('p2p-peers-list');
        if (!tbody) return [];

        try {
            const peers = await apiJson('/api/v1/p2p/peers');
            if (!Array.isArray(peers) || peers.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" class="text-muted text-center py-3">No peers joined yet.</td></tr>';
                return [];
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
                        <td>${peer.file_count ?? 0}</td>
                        <td>${escapeHtml(toLocalDate(peer.last_seen_at))}</td>
                        <td class="text-end">
                            <button type="button" class="btn btn-sm btn-outline-danger p2p-unjoin-peer-btn" data-peer-id="${Number(peer.id)}">
                                <i class="bi bi-x-circle me-1"></i>Unjoin
                            </button>
                        </td>
                    </tr>
                `;
            }).join('');

            tbody.querySelectorAll('.p2p-unjoin-peer-btn').forEach(btn => {
                btn.addEventListener('click', async (event) => {
                    const peerId = parseInt(event.currentTarget.dataset.peerId || '0', 10);
                    if (!peerId) return;
                    await unjoinPeer(peerId);
                });
            });

            return peers;
        } catch (error) {
            console.error('Failed to load peers:', error);
            tbody.innerHTML = '<tr><td colspan="6" class="text-danger text-center py-3">Failed to load peers.</td></tr>';
            return [];
        }
    }

    async function loadStats() {
        try {
            const stats = await apiJson('/api/v1/p2p/stats');
            document.getElementById('p2p-stat-connected').textContent = String(stats.connected_peers || 0);
            document.getElementById('p2p-stat-degraded').textContent = String(stats.degraded_peers || 0);
            document.getElementById('p2p-stat-cluster-files').textContent = String(stats.cluster_file_count || 0);
            document.getElementById('p2p-stat-backend').textContent = String(stats.backend || 'unknown');

            const health = String(stats.health || 'UNKNOWN').toUpperCase();
            const healthBadge = document.getElementById('p2p-health-badge');
            if (healthBadge) {
                healthBadge.textContent = health;
                healthBadge.className = 'badge';
                if (health === 'HEALTHY') {
                    healthBadge.classList.add('text-bg-success');
                } else if (health === 'DEGRADED') {
                    healthBadge.classList.add('text-bg-warning');
                } else if (health === 'IDLE') {
                    healthBadge.classList.add('text-bg-secondary');
                } else {
                    healthBadge.classList.add('text-bg-dark');
                }
            }
        } catch (error) {
            console.error('Failed to load p2p stats:', error);
        }
    }

    function updateJoinEndpointDisplay() {
        const { host, port } = getJoinEndpoint();
        const hostInput = document.getElementById('p2p-connect-host');
        const portInput = document.getElementById('p2p-connect-port');
        if (hostInput) hostInput.value = host;
        if (portInput) portInput.value = port;
    }



    function showRotationResult(payload) {
        const panel = document.getElementById('p2p-rotation-result-panel');
        const updatedRow = document.getElementById('p2p-rotation-updated-row');
        const offlineRow = document.getElementById('p2p-rotation-offline-row');
        if (!panel) return;

        if (updatedRow) {
            const updated = payload.updated_peers || [];
            updatedRow.innerHTML = updated.length > 0
                ? `<span class="text-success"><i class="bi bi-check-circle me-1"></i>Updated: ${escapeHtml(updated.join(', '))}</span>`
                : '<span class="text-muted">No connected peers to update.</span>';
        }
        if (offlineRow) {
            const offline = payload.offline_peers || [];
            offlineRow.innerHTML = offline.length > 0
                ? `<span class="text-warning"><i class="bi bi-exclamation-triangle me-1"></i>Offline (must rejoin manually): ${escapeHtml(offline.join(', '))}</span>`
                : '';
        }

        panel.classList.remove('d-none', 'alert-success', 'alert-warning');
        const hasOffline = (payload.offline_peers || []).length > 0;
        panel.classList.add(hasOffline ? 'alert-warning' : 'alert-success');
    }

    function hideRotationResult() {
        document.getElementById('p2p-rotation-result-panel')?.classList.add('d-none');
    }

    function showGeneratedPsk(psk) {
        const panel = document.getElementById('p2p-generated-psk-panel');
        const input = document.getElementById('p2p-generated-psk-value');
        if (!panel || !input) return;

        if (psk) {
            input.value = psk;
            panel.classList.remove('d-none');
        } else {
            input.value = '';
            panel.classList.add('d-none');
        }
    }

    async function refreshView() {
        const config = await refreshNetworkConfig();
        const peers = await loadPeers();
        await loadStats();

        if (config || peers.length > 0) {
            showManagementMode();
            updateJoinEndpointDisplay();
        } else {
            showWizardMode();
        }
    }

    async function createNetwork(event) {
        event.preventDefault();

        const listenHost = document.getElementById('p2p-wizard-listen-host')?.value?.trim() || '0.0.0.0';
        const listenPort = parseInt(document.getElementById('p2p-wizard-listen-port')?.value || '9119', 10);
        const submitBtn = document.getElementById('p2p-wizard-create-btn');
        const originalText = submitBtn ? submitBtn.innerHTML : '';

        try {
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Creating...';
            }

            const payload = await apiJson('/api/v1/p2p/network', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    network_name: 'File Fridge P2P',
                    listen_host: listenHost,
                    listen_port: listenPort,
                    enabled: true
                })
            });

            showGeneratedPsk(payload.setup_psk || '');
            toastSuccess('P2P network created');
            await refreshView();
        } catch (error) {
            console.error('Failed to create p2p network:', error);
            toastError(error.message);
        } finally {
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerHTML = originalText;
            }
        }
    }

    async function joinNetwork(event) {
        event.preventDefault();

        const host = document.getElementById('p2p-wizard-peer-host')?.value?.trim();
        const port = parseInt(document.getElementById('p2p-wizard-peer-port')?.value || '0', 10);
        const psk = document.getElementById('p2p-wizard-psk')?.value?.trim();
        const scheme = document.getElementById('p2p-wizard-peer-scheme')?.value || 'http';
        const submitBtn = document.getElementById('p2p-wizard-join-btn');
        const originalText = submitBtn ? submitBtn.innerHTML : '';

        if (!host || !port || !psk) {
            toastError('Peer host, peer port, and PSK are required');
            return;
        }

        try {
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Joining...';
            }

            await apiJson('/api/v1/p2p/peers/join', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ host, port, psk, scheme })
            });

            showGeneratedPsk('');
            document.getElementById('p2p-wizard-join-form')?.reset();
            toastSuccess('Joined P2P network');
            await refreshView();
        } catch (error) {
            console.error('Failed to join p2p network:', error);
            toastError(error.message);
        } finally {
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerHTML = originalText;
            }
        }
    }

    async function syncPeers() {
        const syncBtn = document.getElementById('sync-p2p-peers-btn');
        const originalText = syncBtn ? syncBtn.innerHTML : '';

        try {
            if (syncBtn) {
                syncBtn.disabled = true;
                syncBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Syncing...';
            }
            await apiJson('/api/v1/p2p/sync', { method: 'POST' });
            await refreshView();
            toastSuccess('Peer manifests synced');
        } catch (error) {
            console.error('Failed to sync peers:', error);
            toastError(error.message);
        } finally {
            if (syncBtn) {
                syncBtn.disabled = false;
                syncBtn.innerHTML = originalText;
            }
        }
    }

    async function regeneratePsk() {
        const confirmed = await showConfirmModal({
            title: 'Rotate PSK',
            message: 'Generate a new PSK? Online peers will be updated automatically. Offline peers will be disconnected and must rejoin with the new PSK.',
            confirmText: 'Rotate PSK',
            dangerous: true
        });
        if (!confirmed) return;

        const btn = document.getElementById('p2p-regenerate-psk-btn');
        const originalText = btn ? btn.innerHTML : '';
        try {
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Rotating...';
            }

            hideRotationResult();
            const payload = await apiJson('/api/v1/p2p/network/psk/regenerate', { method: 'POST' });
            showGeneratedPsk(payload.psk || '');
            showRotationResult(payload);
            const offlineCount = (payload.offline_peers || []).length;
            if (offlineCount > 0) {
                toastSuccess(`PSK rotated. ${offlineCount} offline peer(s) must rejoin manually.`);
            } else {
                toastSuccess('PSK rotated. All peers updated.');
            }
            await refreshView();
        } catch (error) {
            console.error('Failed to regenerate psk:', error);
            toastError(error.message);
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = originalText;
            }
        }
    }

    async function unjoinNetwork() {
        const confirmed = await showConfirmModal({
            title: 'Unjoin Network',
            message: 'Unjoin this P2P network? This removes this instance from the private network and clears local network configuration.',
            confirmText: 'Unjoin',
            dangerous: true
        });

        if (!confirmed) {
            return;
        }

        const unjoinBtn = document.getElementById('unjoin-p2p-network-btn');
        const originalText = unjoinBtn ? unjoinBtn.innerHTML : '';

        try {
            if (unjoinBtn) {
                unjoinBtn.disabled = true;
                unjoinBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Unjoining...';
            }
            await apiJson('/api/v1/p2p/peers', { method: 'DELETE' });
            showGeneratedPsk('');
            toastSuccess('Unjoined from P2P network');
            await refreshView();
        } catch (error) {
            console.error('Failed to unjoin network:', error);
            toastError(error.message);
        } finally {
            if (unjoinBtn) {
                unjoinBtn.disabled = false;
                unjoinBtn.innerHTML = originalText;
            }
        }
    }

    async function unjoinPeer(peerId) {
        const confirmed = await showConfirmModal({
            title: 'Unjoin Peer',
            message: 'Unjoin this peer from the P2P network?',
            confirmText: 'Unjoin',
            dangerous: true
        });

        if (!confirmed) {
            return;
        }

        const btn = document.querySelector(`.p2p-unjoin-peer-btn[data-peer-id="${peerId}"]`);
        const originalText = btn ? btn.innerHTML : '';

        try {
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>...';
            }
            await apiJson(`/api/v1/p2p/peers/${peerId}`, { method: 'DELETE' });
            toastSuccess('Peer unjoined');
            await refreshView();
        } catch (error) {
            console.error('Failed to unjoin peer:', error);
            toastError(error.message);
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = originalText;
            }
        }
    }

    async function copyGeneratedPsk() {
        const input = document.getElementById('p2p-generated-psk-value');
        if (!input || !input.value) return;
        try {
            await navigator.clipboard.writeText(input.value);
            toastSuccess('PSK copied to clipboard');
        } catch (error) {
            console.error('Failed to copy generated psk:', error);
            toastError('Failed to copy PSK');
        }
    }

    async function copyValue(inputId, label) {
        const input = document.getElementById(inputId);
        if (!input || !input.value) return;
        try {
            await navigator.clipboard.writeText(input.value);
            toastSuccess(`${label} copied`);
        } catch (error) {
            console.error(`Failed to copy ${label}:`, error);
            toastError(`Failed to copy ${label}`);
        }
    }

    async function initP2PSettings() {
        const section = document.getElementById('remote-connections-section');
        if (!section) return;

        document.getElementById('wizard-choice-create')?.addEventListener('click', () => setWizardChoice('create'));
        document.getElementById('wizard-choice-join')?.addEventListener('click', () => setWizardChoice('join'));
        document.getElementById('p2p-wizard-create-form')?.addEventListener('submit', createNetwork);
        document.getElementById('p2p-wizard-join-form')?.addEventListener('submit', joinNetwork);
        document.getElementById('p2p-copy-generated-psk-btn')?.addEventListener('click', () => { void copyGeneratedPsk(); });
        document.getElementById('p2p-copy-connect-host-btn')?.addEventListener('click', () => { void copyValue('p2p-connect-host', 'Host'); });
        document.getElementById('p2p-copy-connect-port-btn')?.addEventListener('click', () => { void copyValue('p2p-connect-port', 'Port'); });

        document.getElementById('sync-p2p-peers-btn')?.addEventListener('click', () => { void syncPeers(); });
        document.getElementById('p2p-regenerate-psk-btn')?.addEventListener('click', () => { void regeneratePsk(); });
        document.getElementById('unjoin-p2p-network-btn')?.addEventListener('click', () => { void unjoinNetwork(); });

        setWizardChoice('create');
        await refreshView();
    }

    if (typeof window.runWhenFileFridgeReady === 'function') {
        window.runWhenFileFridgeReady(() => { void initP2PSettings(); });
    } else {
        void initP2PSettings();
    }
})();
