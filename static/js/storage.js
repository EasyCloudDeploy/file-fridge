// static/js/storage.js

function formatStorageBytes(bytes, decimals = 2) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

function escapeStorageHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function renderStorageStatsList(stats, elementId, emptyMessage) {
    const listEl = document.getElementById(elementId);
    if (!listEl) return;

    if (stats.length === 0) {
        listEl.innerHTML = `<p class="text-muted">${emptyMessage}</p>`;
        return;
    }

    let html = '<ul class="list-group list-group-flush">';
    stats.forEach(s => {
        html += '<li class="list-group-item px-0">';
        if (s.error) {
            html += `
                <div class="d-flex justify-content-between align-items-center">
                    <span class="text-danger text-truncate" title="${escapeStorageHtml(s.path)}"><strong>${escapeStorageHtml(s.path)}</strong></span>
                    <span class="badge bg-danger ms-2">Error</span>
                </div>
                <small class="text-danger">${escapeStorageHtml(s.error)}</small>`;
        } else {
            const usedPercent = (s.used_bytes / s.total_bytes) * 100;
            let progressBarClass = 'bg-success';
            if (usedPercent > 70) {
                progressBarClass = 'bg-danger';
            } else if (usedPercent > 50) {
                progressBarClass = 'bg-warning';
            }

            html += `
                <div>
                    <div class="text-truncate mb-1" title="${escapeStorageHtml(s.path)}"><strong class="small">${escapeStorageHtml(s.path)}</strong></div>
                    <div class="progress" style="height: 16px;">
                        <div class="progress-bar ${progressBarClass}" role="progressbar" style="width: ${usedPercent.toFixed(1)}%;" aria-valuenow="${usedPercent.toFixed(1)}" aria-valuemin="0" aria-valuemax="100">
                            <small>${usedPercent.toFixed(0)}%</small>
                        </div>
                    </div>
                    <div class="d-flex flex-wrap justify-content-between text-muted mt-1" style="font-size: 0.7rem;">
                        <span>${formatStorageBytes(s.used_bytes)} used</span>
                        <span>${formatStorageBytes(s.free_bytes)} free</span>
                    </div>
                </div>`;
        }
        html += '</li>';
    });
    html += '</ul>';

    listEl.innerHTML = html;
}

async function loadHotStorageStats() {
    const listEl = document.getElementById('hotStorageStatusList');
    if (!listEl) return;

    try {
        const response = await fetch('/api/v1/paths/stats');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const stats = await response.json();
        renderStorageStatsList(stats, 'hotStorageStatusList', 'No monitored paths configured.');
    } catch (error) {
        console.error('Error fetching hot storage stats:', error);
        listEl.innerHTML = '<p class="text-danger">Could not load hot storage statistics.</p>';
    }
}

async function loadColdStorageStats() {
    const listEl = document.getElementById('storageStatusList');
    if (!listEl) return;

    try {
        const response = await fetch('/api/v1/storage/stats');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const stats = await response.json();
        renderStorageStatsList(stats, 'storageStatusList', 'No cold storage paths configured.');
    } catch (error) {
        console.error('Error fetching cold storage stats:', error);
        listEl.innerHTML = '<p class="text-danger">Could not load cold storage statistics.</p>';
    }
}

function loadAllStorageStats() {
    loadHotStorageStats();
    loadColdStorageStats();
}

document.addEventListener('DOMContentLoaded', loadAllStorageStats);
