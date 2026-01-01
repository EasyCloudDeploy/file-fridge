// Statistics JavaScript - loads data via API
document.addEventListener('DOMContentLoaded', function() {
    loadStats();
});

function loadStats() {
    // Load overall stats
    Promise.all([
        fetch('/api/v1/stats').then(r => r.json()),
        fetch('/api/v1/stats/aggregated?period=daily&days=30').then(r => r.json())
    ])
    .then(([stats, aggregated]) => {
        updateStats(stats);
        updateChart(aggregated);
        updatePathStats(stats);
    })
    .catch(error => {
        console.error('Error loading stats:', error);
        showError('Failed to load statistics');
    });
}

function updateStats(stats) {
    // Update total files
    const totalFilesEl = document.getElementById('totalFiles');
    if (totalFilesEl) {
        totalFilesEl.textContent = stats.total_files_moved || 0;
    }
    
    // Update total size
    const totalSizeEl = document.getElementById('totalSize');
    if (totalSizeEl) {
        totalSizeEl.textContent = formatBytes(stats.total_size_moved || 0);
    }
}

function updateChart(aggregated) {
    const ctx = document.getElementById('dailyChart');
    if (!ctx) return;
    
    const chartCtx = ctx.getContext('2d');
    
    // Process data for chart
    const labels = aggregated.data.map(d => d.period);
    const counts = aggregated.data.map(d => d.count);
    
    new Chart(chartCtx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Files Moved',
                data: counts,
                borderColor: 'rgb(75, 192, 192)',
                backgroundColor: 'rgba(75, 192, 192, 0.2)',
                tension: 0.1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            scales: {
                y: {
                    beginAtZero: true
                }
            }
        }
    });
}

function updatePathStats(stats) {
    const pathStatsBody = document.getElementById('pathStatsBody');
    if (!pathStatsBody) return;
    
    const filesByPath = stats.files_by_path || {};
    const paths = Object.keys(filesByPath);
    
    if (paths.length === 0) {
        pathStatsBody.innerHTML = '<tr><td colspan="3" class="text-muted">No statistics available yet.</td></tr>';
        return;
    }
    
    pathStatsBody.innerHTML = paths.map(pathName => {
        const stat = filesByPath[pathName];
        return `
            <tr>
                <td><strong>${escapeHtml(pathName)}</strong></td>
                <td>${stat.count || 0}</td>
                <td>${formatBytes(stat.size || 0)}</td>
            </tr>
        `;
    }).join('');
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

function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    if (bytes < 1024) return bytes + ' Bytes';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(2) + ' MB';
    return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB';
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

