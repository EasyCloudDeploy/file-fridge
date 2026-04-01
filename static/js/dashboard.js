// Dashboard JavaScript - loads data via API
const DASHBOARD_REFRESH_INTERVAL_MS = 2000;

let dashboardRefreshInterval = null;
let dashboardRefreshInFlight = false;

document.addEventListener('DOMContentLoaded', function () {
    refreshDashboard().catch(error => {
        console.error('Initial dashboard refresh failed:', error);
    });
    startDashboardAutoRefresh();
});

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

    pathsList.innerHTML = `
        <div class="table-responsive">
            <table class="table table-sm table-hover">
                <thead>
                    <tr>
                        <th>Name</th>
                        <th>Status</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>
                    ${paths.map(path => `
                        <tr ${path.error_message ? 'class="table-danger"' : ''}>
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
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
    `;
}

function updateRecentFiles(files) {
    const recentFilesList = document.getElementById('recentFilesList');
    if (!recentFilesList) return;

    const recentFiles = files.slice(0, 10);

    if (recentFiles.length === 0) {
        recentFilesList.innerHTML = '<p class="text-muted">No recent activity.</p>';
        return;
    }

    recentFilesList.innerHTML = recentFiles.map(file => `
        <tr>
            <td style="max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(file.original_path)}">
                <code class="small">${escapeHtml(file.original_path)}</code>
            </td>
            <td class="d-none d-sm-table-cell">${formatBytes(file.file_size)}</td>
            <td><small>${formatDate(file.moved_at)}</small></td>
        </tr>
    `).join('');
}

function showError(message) {
    const alertDiv = document.createElement('div');
    alertDiv.className = 'alert alert-danger alert-dismissible fade show';
    alertDiv.innerHTML = `
        ${escapeHtml(message)}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    const container = document.querySelector('main.container-fluid');
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
