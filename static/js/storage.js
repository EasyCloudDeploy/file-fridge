// static/js/storage.js

function formatBytes(bytes, decimals = 2) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

async function loadStorageStats() {
    const storageListEl = document.getElementById('storageStatusList');
    if (!storageListEl) return;

    try {
        const response = await fetch('/api/v1/storage/stats');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const stats = await response.json();

        if (stats.length === 0) {
            storageListEl.innerHTML = '<p class="text-muted">No cold storage paths configured.</p>';
            return;
        }

        let html = '<ul class="list-group">';
        stats.forEach(s => {
            html += '<li class="list-group-item">';
            if (s.error) {
                html += `
                    <div class="d-flex justify-content-between align-items-center">
                        <span class="text-danger"><strong>${s.path}</strong></span>
                        <span class="badge bg-danger">Error: ${s.error}</span>
                    </div>`;
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
                        <strong>${s.path}</strong>
                        <div class="progress mt-2" style="height: 20px;">
                            <div class="progress-bar ${progressBarClass}" role="progressbar" style="width: ${usedPercent.toFixed(2)}%;" aria-valuenow="${usedPercent.toFixed(2)}" aria-valuemin="0" aria-valuemax="100">
                                ${usedPercent.toFixed(2)}%
                            </div>
                        </div>
                        <div class="d-flex justify-content-between text-muted small mt-1">
                            <span>Used: ${formatBytes(s.used_bytes)}</span>
                            <span>Free: ${formatBytes(s.free_bytes)}</span>
                            <span>Total: ${formatBytes(s.total_bytes)}</span>
                        </div>
                    </div>`;
            }
            html += '</li>';
        });
        html += '</ul>';

        storageListEl.innerHTML = html;

    } catch (error) {
        console.error('Error fetching storage stats:', error);
        storageListEl.innerHTML = '<p class="text-danger">Could not load storage statistics.</p>';
    }
}

document.addEventListener('DOMContentLoaded', loadStorageStats);
