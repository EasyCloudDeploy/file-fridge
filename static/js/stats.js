// Statistics JavaScript - loads data via API
document.addEventListener('DOMContentLoaded', function() {
    loadStats();
});

function loadStats() {
    // Load detailed stats (includes all comprehensive metrics)
    fetch('/api/v1/stats/detailed')
        .then(r => r.json())
        .then(stats => {
            updateStats(stats);
            updateChart(stats.daily_activity);
            updatePathStats(stats);
            updateAdditionalMetrics(stats);
        })
        .catch(error => {
            console.error('Error loading stats:', error);
            showError('Failed to load statistics');
        });
}

function updateStats(stats) {
    // Update total files moved
    const totalFilesEl = document.getElementById('totalFiles');
    if (totalFilesEl) {
        totalFilesEl.textContent = stats.total_files_moved || 0;
    }

    // Update total size moved
    const totalSizeEl = document.getElementById('totalSize');
    if (totalSizeEl) {
        totalSizeEl.textContent = formatBytes(stats.total_size_moved || 0);
    }

    // Update hot storage metrics
    const hotFilesEl = document.getElementById('hotFiles');
    if (hotFilesEl) {
        hotFilesEl.textContent = stats.total_files_hot || 0;
    }

    const hotSizeEl = document.getElementById('hotSize');
    if (hotSizeEl) {
        hotSizeEl.textContent = formatBytes(stats.total_size_hot || 0);
    }

    // Update cold storage metrics
    const coldFilesEl = document.getElementById('coldFiles');
    if (coldFilesEl) {
        coldFilesEl.textContent = stats.total_files_cold || 0;
    }

    const coldSizeEl = document.getElementById('coldSize');
    if (coldSizeEl) {
        coldSizeEl.textContent = formatBytes(stats.total_size_cold || 0);
    }

    // Update performance metrics
    const files24hEl = document.getElementById('files24h');
    if (files24hEl) {
        files24hEl.textContent = stats.files_moved_last_24h || 0;
    }

    const avgPerDayEl = document.getElementById('avgPerDay');
    if (avgPerDayEl) {
        avgPerDayEl.textContent = Math.round(stats.average_files_per_day || 0);
    }
}

function updateAdditionalMetrics(stats) {
    // Update operational metrics if elements exist
    const activePathsEl = document.getElementById('activePaths');
    if (activePathsEl) {
        activePathsEl.textContent = `${stats.active_paths || 0} / ${stats.total_paths || 0}`;
    }

    const spaceSavedEl = document.getElementById('spaceSaved');
    if (spaceSavedEl) {
        spaceSavedEl.textContent = formatBytes(stats.space_saved || 0);
    }

    const avgFileSizeEl = document.getElementById('avgFileSize');
    if (avgFileSizeEl) {
        avgFileSizeEl.textContent = formatBytes(stats.average_file_size || 0);
    }
}

function updateChart(dailyActivity) {
    const ctx = document.getElementById('dailyChart');
    if (!ctx) return;

    const chartCtx = ctx.getContext('2d');

    // Show loading indicator
    const loadingDiv = document.getElementById('daily-chart-loading');
    if (loadingDiv) loadingDiv.style.display = 'none';
    ctx.style.display = 'block';

    // Process data for chart
    const labels = dailyActivity.map(d => d.date);
    const counts = dailyActivity.map(d => d.files_moved);
    const sizes = dailyActivity.map(d => d.size_moved / (1024 * 1024 * 1024)); // Convert to GB

    new Chart(chartCtx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Files Moved',
                    data: counts,
                    borderColor: 'rgb(75, 192, 192)',
                    backgroundColor: 'rgba(75, 192, 192, 0.2)',
                    tension: 0.1,
                    yAxisID: 'y'
                },
                {
                    label: 'Size Moved (GB)',
                    data: sizes,
                    borderColor: 'rgb(255, 99, 132)',
                    backgroundColor: 'rgba(255, 99, 132, 0.2)',
                    tension: 0.1,
                    yAxisID: 'y1'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            interaction: {
                mode: 'index',
                intersect: false
            },
            scales: {
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    beginAtZero: true,
                    title: {
                        display: true,
                        text: 'Files Moved'
                    }
                },
                y1: {
                    type: 'linear',
                    display: true,
                    position: 'right',
                    beginAtZero: true,
                    title: {
                        display: true,
                        text: 'Size (GB)'
                    },
                    grid: {
                        drawOnChartArea: false
                    }
                }
            }
        }
    });
}

function updatePathStats(stats) {
    const pathStatsTable = document.getElementById('pathStatsTable');
    const pathStatsContent = document.getElementById('path-stats-content');
    const pathStatsLoading = document.getElementById('path-stats-loading');
    const noPathStatsMessage = document.getElementById('no-path-stats-message');

    if (!pathStatsTable) return;

    // Hide loading, show content
    if (pathStatsLoading) pathStatsLoading.style.display = 'none';

    const topPaths = stats.top_paths_by_files || [];

    if (topPaths.length === 0) {
        if (pathStatsContent) pathStatsContent.style.display = 'none';
        if (noPathStatsMessage) noPathStatsMessage.style.display = 'block';
        return;
    }

    if (pathStatsContent) pathStatsContent.style.display = 'block';
    if (noPathStatsMessage) noPathStatsMessage.style.display = 'none';

    const tbody = pathStatsTable.querySelector('tbody');
    if (!tbody) return;

    tbody.innerHTML = topPaths.map(path => {
        return `
            <tr>
                <td><strong>${escapeHtml(path.path_name)}</strong></td>
                <td>${path.file_count || 0}</td>
                <td>${formatBytes(path.total_size || 0)}</td>
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

