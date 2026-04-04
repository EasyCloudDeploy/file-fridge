// Dashboard JavaScript - loads data via API
const DASHBOARD_REFRESH_INTERVAL_MS = 10000;

let dashboardRefreshInterval = null;
let dashboardRefreshInFlight = false;
let dashboardInitialized = false;

function initDashboard() {
    if (dashboardInitialized) {
        return;
    }
    dashboardInitialized = true;

    refreshDashboard().catch(error => {
        console.error('Initial dashboard refresh failed:', error);
    });
    startDashboardAutoRefresh();
}

window.runWhenFileFridgeReady(initDashboard);

document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
        stopDashboardAutoRefresh();
        return;
    }

    refreshDashboard().catch(error => {
        console.error('Dashboard refresh after tab visibility change failed:', error);
    });
    startDashboardAutoRefresh();
});

window.addEventListener('beforeunload', stopDashboardAutoRefresh);

function startDashboardAutoRefresh() {
    if (dashboardRefreshInterval !== null) {
        return;
    }

    dashboardRefreshInterval = window.setInterval(() => {
        if (!document.hidden) {
            refreshDashboard().catch(error => {
                console.error('Scheduled dashboard refresh failed:', error);
            });
        }
    }, DASHBOARD_REFRESH_INTERVAL_MS);
}

function stopDashboardAutoRefresh() {
    if (dashboardRefreshInterval === null) {
        return;
    }

    window.clearInterval(dashboardRefreshInterval);
    dashboardRefreshInterval = null;
}

async function refreshDashboard() {
    if (dashboardRefreshInFlight) {
        return;
    }

    dashboardRefreshInFlight = true;

    try {
        await loadDashboardData();
        if (
            typeof loadHotStorageStats === 'function'
            && typeof loadColdStorageStats === 'function'
        ) {
            await Promise.all([loadHotStorageStats(), loadColdStorageStats()]);
        }
    } finally {
        dashboardRefreshInFlight = false;
    }
}

async function getResponseErrorDetails(response) {
    try {
        const body = await response.text();
        return body ? `: ${body}` : '';
    } catch (_error) {
        return '';
    }
}

async function loadDashboardData() {
    // Load overall stats
    return authenticatedFetch('/api/v1/stats')
        .then(async response => {
            if (!response.ok) {
                const details = await getResponseErrorDetails(response);
                throw new Error(
                    `Failed to load dashboard stats (${response.status})${details}`
                );
            }
            return response.json();
        })
        .then(data => {
            updateStats(data);
            return loadPaths();
        })
        .catch(error => {
            console.error('Error loading dashboard data:', error);
            showError('Failed to load dashboard data');
            throw error;
        });
}

function updateStats(stats) {
    // Update total files
    const totalFilesEl = document.getElementById('totalFiles');
    if (totalFilesEl) {
        totalFilesEl.textContent = stats.total_files_moved || 0;
    }

    // Update hot files
    const totalFilesHotEl = document.getElementById('totalFilesHot');
    if (totalFilesHotEl) {
        totalFilesHotEl.textContent = stats.total_files_hot || 0;
    }

    // Update cold files
    const totalFilesColdEl = document.getElementById('totalFilesCold');
    if (totalFilesColdEl) {
        totalFilesColdEl.textContent = stats.total_files_cold || 0;
    }

    // Update total size
    const totalSizeEl = document.getElementById('totalSize');
    if (totalSizeEl) {
        totalSizeEl.textContent = formatBytes(stats.total_size_moved || 0);
    }

    // Update recent count (last 24 hours)
    const recentCountEl = document.getElementById('recentCount');
    if (recentCountEl && stats.recent_activity) {
        const yesterday = new Date();
        yesterday.setDate(yesterday.getDate() - 1);
        const recent = stats.recent_activity.filter(file => {
            const fileDate = new Date(file.moved_at);
            return fileDate >= yesterday;
        });
        recentCountEl.textContent = recent.length;
    }

    // Update recent files
    updateRecentFiles(stats.recent_activity || []);
}

function updatePathsCount(count) {
    const pathsCountEl = document.getElementById('pathsCount');
    if (pathsCountEl) {
        pathsCountEl.textContent = count;
    }
}

function loadPaths() {
    return authenticatedFetch('/api/v1/paths')
        .then(async response => {
            if (!response.ok) {
                const details = await getResponseErrorDetails(response);
                throw new Error(`Failed to load paths (${response.status})${details}`);
            }
            return response.json();
        })
        .then(paths => {
            updatePaths(paths);
        })
        .catch(error => {
            console.error('Error loading paths:', error);
            throw error;
        });
}

function updatePaths(paths) {
    const pathsList = document.getElementById('pathsList');
    if (!pathsList) return;

    updatePathsCount(paths.length);

    if (paths.length === 0) {
        pathsList.innerHTML = `
            <p class="text-muted">No monitored paths configured yet.</p>
            <a href="/paths/new" class="btn btn-primary">
                <i class="bi bi-plus-circle"></i> Add Path
            </a>
        `;
        return;
    }

    let tableWrapper = pathsList.querySelector('[data-dashboard-paths-table]');
    let tbody = pathsList.querySelector('[data-dashboard-paths-body]');

    if (!tableWrapper || !tbody) {
        pathsList.innerHTML = `
            <div class="table-responsive" data-dashboard-paths-table>
                <table class="table table-sm table-hover">
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Status</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody data-dashboard-paths-body></tbody>
                </table>
            </div>
        `;
        tableWrapper = pathsList.querySelector('[data-dashboard-paths-table]');
        tbody = pathsList.querySelector('[data-dashboard-paths-body]');
    }

    if (!tableWrapper || !tbody) {
        return;
    }

    const activePathIds = new Set(paths.map(path => String(path.id)));

    paths.forEach(path => {
        let row = tbody.querySelector(`[data-path-id="${path.id}"]`);
        if (!row) {
            row = document.createElement('tr');
            row.dataset.pathId = String(path.id);
            tbody.appendChild(row);
        }

        row.className = path.error_message ? 'table-danger' : '';
        row.innerHTML = `
            <td style="max-width: 180px;">
                <strong class="small">${escapeHtml(path.name)}</strong><br>
                <small class="text-muted text-truncate d-block" style="max-width: 160px;" title="${escapeHtml(path.source_path)}">${escapeHtml(path.source_path)}</small>
                ${path.error_message ? `
                    <div class="alert alert-danger alert-sm mt-1 mb-0 py-1 px-2 d-none d-md-block" role="alert" style="font-size: 0.7rem;">
                        <i class="bi bi-exclamation-triangle-fill"></i> ${escapeHtml(path.error_message)}
                    </div>
                ` : ''}
            </td>
            <td>
                <span class="badge bg-${path.enabled ? 'success' : 'secondary'}">${path.enabled ? 'On' : 'Off'}</span>
                ${path.error_message ? `
                    <br><span class="badge bg-danger mt-1">
                        <i class="bi bi-exclamation-triangle-fill"></i>
                    </span>
                ` : ''}
            </td>
            <td>
                <a href="/paths/${path.id}" class="btn btn-sm btn-outline-primary" title="View">
                    <i class="bi bi-eye"></i>
                </a>
            </td>
        `;
    });

    Array.from(tbody.querySelectorAll('[data-path-id]')).forEach(row => {
        if (!activePathIds.has(row.dataset.pathId || '')) {
            row.remove();
        }
    });
}

function updateRecentFiles(files) {
    const recentFilesList = document.getElementById('recentFilesList');
    if (!recentFilesList) return;

    const recentFiles = files.slice(0, 10);

    if (recentFiles.length === 0) {
        recentFilesList.innerHTML = `
            <tr>
                <td colspan="3" class="text-center text-muted">No recent activity yet.</td>
            </tr>
        `;
        return;
    }

    const activeFileKeys = new Set(
        recentFiles.map(file => `${file.original_path}::${file.moved_at}`)
    );

    recentFiles.forEach(file => {
        const rowKey = `${file.original_path}::${file.moved_at}`;
        let row = recentFilesList.querySelector(
            `[data-file-key="${CSS.escape(rowKey)}"]`
        );
        if (!row) {
            row = document.createElement('tr');
            row.dataset.fileKey = rowKey;
            recentFilesList.appendChild(row);
        }

        row.innerHTML = `
            <td style="max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(file.original_path)}">
                <code class="small">${escapeHtml(file.original_path)}</code>
            </td>
            <td class="d-none d-sm-table-cell">${formatBytes(file.file_size)}</td>
            <td><small>${formatDate(file.moved_at)}</small></td>
        `;
    });

    Array.from(recentFilesList.querySelectorAll('[data-file-key]')).forEach(row => {
        if (!activeFileKeys.has(row.dataset.fileKey || '')) {
            row.remove();
        }
    });
}

function showError(message) {
    const alertDiv = document.createElement('div');
    alertDiv.className = 'alert alert-danger alert-dismissible fade show';
    alertDiv.innerHTML = `
        ${escapeHtml(message)}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    const container = document.getElementById('main-content');
    if (container) {
        container.insertBefore(alertDiv, container.firstChild);
        setTimeout(() => alertDiv.remove(), 5000);
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    if (bytes < 1024) return bytes + ' Bytes';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(2) + ' MB';
    return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB';
}

function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleString();
}
