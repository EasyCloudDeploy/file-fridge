// Paths management JavaScript - client-side rendering
const API_BASE_URL = '/api/v1';

function formatBytes(bytes, decimals = 2) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

// Progress polling state
let progressPollingInterval = null;
let currentPathId = null;

// Utility functions
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Notification function - uses toast notifications from app.js
function showNotification(message, type = 'success') {
    showToast(message, type);
}

// Load and render paths list
async function loadPathsList() {
    const loadingEl = document.getElementById('paths-loading');
    const contentEl = document.getElementById('paths-content');
    const emptyEl = document.getElementById('no-paths-message');
    const tableBody = document.querySelector('#pathsTable tbody');
    
    if (loadingEl) loadingEl.style.display = 'block';
    if (contentEl) contentEl.style.display = 'none';
    if (emptyEl) emptyEl.style.display = 'none';
    if (tableBody) tableBody.innerHTML = '';
    
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/paths`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const paths = await response.json();
        
        if (paths.length === 0) {
            if (emptyEl) emptyEl.style.display = 'block';
        } else {
            if (tableBody) {
                paths.forEach(path => {
                    const row = tableBody.insertRow();
                    if (path.error_message) {
                        row.classList.add('table-danger');
                    }
                    // Format last scan info
                    let lastScanDisplay = '<span class="text-muted">Never</span>';
                    if (path.last_scan_at) {
                        const scanDate = new Date(path.last_scan_at);
                        const statusBadge = path.last_scan_status === 'success'
                            ? '<span class="badge bg-success"><i class="bi bi-check-circle"></i></span>'
                            : path.last_scan_status === 'failure'
                                ? `<span class="badge bg-danger" style="cursor: pointer;" onclick="showScanErrors(${path.id})" title="View Errors"><i class="bi bi-exclamation-triangle"></i></span>`
                                : path.last_scan_status === 'pending'
                                    ? '<span class="badge bg-warning"><i class="bi bi-hourglass-split"></i></span>'
                                    : '';
                        lastScanDisplay = `${statusBadge} <span class="small">${scanDate.toLocaleDateString()} ${scanDate.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}</span>`;
                    }
                    row.innerHTML = `
                        <td>
                            <strong>${escapeHtml(path.name)}</strong>
                            ${path.error_message ? `
                                <div class="alert alert-danger alert-sm mt-1 mb-0 py-1 px-2 d-none d-md-block" role="alert">
                                    <i class="bi bi-exclamation-triangle-fill"></i> <strong>Error:</strong> ${escapeHtml(path.error_message)}
                                </div>
                            ` : ''}
                        </td>
                        <td class="d-none d-md-table-cell"><code class="small">${escapeHtml(path.source_path)}</code></td>
                        <td class="d-none d-lg-table-cell">
                            ${path.storage_locations && path.storage_locations.length > 0
                                ? (path.storage_locations.length === 1
                                    ? `<code class="small">${escapeHtml(path.storage_locations[0].path)}</code>`
                                    : `<code class="small">${escapeHtml(path.storage_locations[0].path)}</code> <span class="badge bg-secondary">+${path.storage_locations.length - 1}</span>`
                                  )
                                : '<span class="text-muted">None</span>'
                            }
                        </td>
                        <td class="d-none d-sm-table-cell"><span class="badge bg-info">${escapeHtml(path.operation_type)}</span></td>
                        <td class="d-none d-lg-table-cell">${Math.floor(path.check_interval_seconds / 60)} min</td>
                        <td>
                            <span class="badge bg-${path.enabled ? 'success' : 'secondary'}">
                                ${path.enabled ? 'Enabled' : 'Disabled'}
                            </span>
                            ${path.error_message ? `
                                <br><span class="badge bg-danger mt-1">
                                    <i class="bi bi-exclamation-triangle-fill"></i><span class="d-none d-sm-inline"> Error</span>
                                </span>
                            ` : ''}
                        </td>
                        <td class="d-none d-md-table-cell">${lastScanDisplay}</td>
                        <td>
                            <div class="btn-group btn-group-sm">
                                <a href="/paths/${path.id}" class="btn btn-outline-primary" title="View">
                                    <i class="bi bi-eye"></i>
                                </a>
                                <a href="/paths/${path.id}/edit" class="btn btn-outline-secondary" title="Edit">
                                    <i class="bi bi-pencil"></i>
                                </a>
                                <button type="button" class="btn btn-outline-info scan-btn-${path.id}" onclick="triggerScan(${path.id}, this)" title="Scan Now" ${path.error_message ? 'disabled' : ''}>
                                    <i class="bi bi-arrow-repeat"></i>
                                </button>
                                <button type="button" class="btn btn-outline-danger" onclick="deletePath(${path.id})" title="Delete">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </div>
                        </td>
                    `;
                });
            }
            if (contentEl) contentEl.style.display = 'block';
        }
    } catch (error) {
        console.error('Error loading paths:', error);
        showNotification(`Failed to load paths: ${error.message}`, 'error');
        if (emptyEl) emptyEl.style.display = 'block';
    } finally {
        if (loadingEl) loadingEl.style.display = 'none';
    }
}

// Show scan errors modal with lazy loading
async function showScanErrors(pathId) {
    const modal = new bootstrap.Modal(document.getElementById('scanErrorsModal'));
    const loadingEl = document.getElementById('scan-errors-loading');
    const contentEl = document.getElementById('scan-errors-content');
    const emptyEl = document.getElementById('scan-errors-empty');

    // Reset modal state
    if (loadingEl) loadingEl.style.display = 'block';
    if (contentEl) contentEl.style.display = 'none';
    if (emptyEl) emptyEl.style.display = 'none';

    // Show modal immediately with loading state
    modal.show();

    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/paths/${pathId}/scan-errors`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const data = await response.json();

        // Update modal content
        document.getElementById('scan-errors-path-name').textContent = data.path_name;
        document.getElementById('scan-errors-last-scan').textContent = data.last_scan_at
            ? new Date(data.last_scan_at).toLocaleString()
            : 'Never';

        const statusEl = document.getElementById('scan-errors-status');
        if (statusEl) {
            statusEl.textContent = data.last_scan_status || 'Unknown';
            statusEl.className = 'badge ' + (data.last_scan_status === 'success' ? 'bg-success' :
                data.last_scan_status === 'failure' ? 'bg-danger' :
                data.last_scan_status === 'pending' ? 'bg-warning' : 'bg-secondary');
        }

        if (data.last_scan_error_log) {
            document.getElementById('scan-errors-log').textContent = data.last_scan_error_log;
            if (contentEl) contentEl.style.display = 'block';
        } else {
            if (emptyEl) emptyEl.style.display = 'block';
        }
    } catch (error) {
        console.error('Error loading scan errors:', error);
        showNotification(`Failed to load scan errors: ${error.message}`, 'error');
        modal.hide();
    } finally {
        if (loadingEl) loadingEl.style.display = 'none';
    }
}

// Load and render path detail
async function loadPathDetail(pathId) {
    const loadingEl = document.getElementById('path-loading');
    const contentEl = document.getElementById('path-content');
    const errorEl = document.getElementById('path-error');
    
    if (loadingEl) loadingEl.style.display = 'block';
    if (contentEl) contentEl.style.display = 'none';
    if (errorEl) errorEl.style.display = 'none';
    
    try {
        const [pathResponse, criteriaResponse, storageResponse, hotStorageResponse] = await Promise.all([
            authenticatedFetch(`${API_BASE_URL}/paths/${pathId}`),
            authenticatedFetch(`${API_BASE_URL}/criteria/path/${pathId}`),
            authenticatedFetch(`${API_BASE_URL}/storage/stats`),
            authenticatedFetch(`${API_BASE_URL}/paths/stats`)
        ]);
        
        if (!pathResponse.ok) {
            if (pathResponse.status === 404) {
                window.location.href = '/paths';
                return;
            }
            throw new Error(`HTTP error! status: ${pathResponse.status}`);
        }
        
        const path = await pathResponse.json();
        const criteria = await criteriaResponse.ok ? await criteriaResponse.json() : [];
        const storageStats = await storageResponse.ok ? await storageResponse.json() : [];
        const hotStorageStats = await hotStorageResponse.ok ? await hotStorageResponse.json() : [];
        
        // Update page title
        document.title = `${path.name} - File Fridge`;
        
        // Show error state if present
        if (path.error_message) {
            const errorAlert = document.createElement('div');
            errorAlert.className = 'alert alert-danger alert-dismissible fade show';
            errorAlert.setAttribute('role', 'alert');
            errorAlert.innerHTML = `
                <h5 class="alert-heading">
                    <i class="bi bi-exclamation-triangle-fill"></i> Path in Error State
                </h5>
                <p class="mb-0"><strong>This path is not processing files due to a configuration error:</strong></p>
                <p class="mb-2">${escapeHtml(path.error_message)}</p>
                <hr>
                <p class="mb-0"><small>Please fix the configuration issue to resume file processing. You can edit the path or criteria to resolve this.</small></p>
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            `;
            const mainContent = document.querySelector('main.container-fluid');
            if (mainContent) {
                mainContent.insertBefore(errorAlert, mainContent.firstChild);
            }
        }
        
        // Render path details
        const pathNameEl = document.getElementById('path-name');
        if (pathNameEl) {
            pathNameEl.innerHTML = `
                ${escapeHtml(path.name)}
                ${path.error_message ? `
                    <span class="badge bg-danger ms-2">
                        <i class="bi bi-exclamation-triangle-fill"></i> Error State
                    </span>
                ` : ''}
            `;
        }
        
        const pathConfigBody = document.querySelector('#pathConfigTable tbody');
        if (pathConfigBody) {
            pathConfigBody.innerHTML = `
                <tr>
                    <th width="40%">Name:</th>
                    <td><strong>${escapeHtml(path.name)}</strong></td>
                </tr>
                <tr>
                    <th>Source Path:</th>
                    <td><code>${escapeHtml(path.source_path)}</code></td>
                </tr>
                <tr>
                    <th>Cold Storage:</th>
                    <td>
                        ${path.storage_locations && path.storage_locations.length > 0
                            ? path.storage_locations.map(loc =>
                                `<div><span class="badge bg-secondary me-1">${escapeHtml(loc.name)}</span> <code>${escapeHtml(loc.path)}</code></div>`
                              ).join('')
                            : '<span class="text-muted">No storage locations configured</span>'
                        }
                    </td>
                </tr>
                <tr>
                    <th>Operation Type:</th>
                    <td><span class="badge bg-info">${escapeHtml(path.operation_type)}</span></td>
                </tr>
                <tr>
                    <th>Check Interval:</th>
                    <td>${Math.floor(path.check_interval_seconds / 60)} minutes</td>
                </tr>
                <tr>
                    <th>Status:</th>
                    <td>
                        <span class="badge bg-${path.enabled ? 'success' : 'secondary'}">
                            ${path.enabled ? 'Enabled' : 'Disabled'}
                        </span>
                        ${path.error_message ? `
                            <span class="badge bg-danger ms-2">
                                <i class="bi bi-exclamation-triangle-fill"></i> Error State
                            </span>
                        ` : ''}
                    </td>
                </tr>
                ${path.error_message ? `
                    <tr class="table-danger">
                        <th>Error Message:</th>
                        <td>
                            <div class="alert alert-danger mb-0 py-2" role="alert">
                                <i class="bi bi-exclamation-triangle-fill"></i> ${escapeHtml(path.error_message)}
                            </div>
                        </td>
                    </tr>
                ` : ''}
                <tr>
                    <th>Created:</th>
                    <td>${new Date(path.created_at).toLocaleString()}</td>
                </tr>
            `;
        }
        
        // Render hot storage status (source path)
        const hotStorageCardBody = document.getElementById('hot-storage-status-card');
        if (hotStorageCardBody) {
            // Find stats for the source path
            const sourceStat = hotStorageStats.find(s =>
                path.source_path.startsWith(s.path) || s.path.startsWith(path.source_path)
            );

            if (!sourceStat) {
                hotStorageCardBody.innerHTML = '<p class="text-muted">Storage stats not available.</p>';
            } else if (sourceStat.error) {
                hotStorageCardBody.innerHTML = `
                    <div class="mb-2">
                        <div class="text-truncate mb-1" title="${escapeHtml(path.source_path)}"><strong>${escapeHtml(path.source_path)}</strong></div>
                        <div class="alert alert-danger mb-0 py-2">
                            <strong>Error:</strong> ${escapeHtml(sourceStat.error)}
                        </div>
                    </div>`;
            } else {
                const usedPercent = (sourceStat.used_bytes / sourceStat.total_bytes) * 100;
                let progressBarClass = 'bg-success';
                if (usedPercent > 70) {
                    progressBarClass = 'bg-danger';
                } else if (usedPercent > 50) {
                    progressBarClass = 'bg-warning';
                }

                hotStorageCardBody.innerHTML = `
                    <div class="mb-2">
                        <div class="text-truncate mb-1" title="${escapeHtml(path.source_path)}"><strong>${escapeHtml(path.source_path)}</strong></div>
                        <div class="progress" style="height: 18px;">
                            <div class="progress-bar ${progressBarClass}" role="progressbar" style="width: ${usedPercent.toFixed(1)}%;" aria-valuenow="${usedPercent.toFixed(1)}" aria-valuemin="0" aria-valuemax="100">
                                ${usedPercent.toFixed(1)}%
                            </div>
                        </div>
                        <div class="d-flex justify-content-between text-muted small mt-1">
                            <span>Used: ${formatBytes(sourceStat.used_bytes)}</span>
                            <span>Free: ${formatBytes(sourceStat.free_bytes)}</span>
                            <span>Total: ${formatBytes(sourceStat.total_bytes)}</span>
                        </div>
                    </div>`;
            }
        }

        // Render storage status
        const storageCardBody = document.getElementById('storage-status-card');
        if (storageCardBody) {
            if (!path.storage_locations || path.storage_locations.length === 0) {
                storageCardBody.innerHTML = '<p class="text-muted">No storage locations configured.</p>';
            } else {
                // Find stats for all storage locations
                const locationStats = path.storage_locations.map(loc => {
                    const stat = storageStats.find(s => loc.path.startsWith(s.path) || s.path.startsWith(loc.path));
                    return { location: loc, stat: stat };
                });

                storageCardBody.innerHTML = locationStats.map(({location, stat}) => {
                    if (!stat) {
                        return `
                            <div class="mb-3">
                                <strong>${escapeHtml(location.name)}</strong>
                                <p class="text-muted small"><code>${escapeHtml(location.path)}</code></p>
                                <p class="text-muted">Storage stats not available.</p>
                            </div>`;
                    }

                    if (stat.error) {
                        return `
                            <div class="mb-3">
                                <strong>${escapeHtml(location.name)}</strong>
                                <p class="text-muted small"><code>${escapeHtml(location.path)}</code></p>
                                <div class="alert alert-danger mb-0">
                                    <strong>Error:</strong> ${escapeHtml(stat.error)}
                                </div>
                            </div>`;
                    }

                    const usedPercent = (stat.used_bytes / stat.total_bytes) * 100;
                    let progressBarClass = 'bg-success';
                    if (usedPercent > 70) {
                        progressBarClass = 'bg-danger';
                    } else if (usedPercent > 50) {
                        progressBarClass = 'bg-warning';
                    }

                    return `
                        <div class="mb-3">
                            <strong>${escapeHtml(location.name)}</strong>
                            <p class="text-muted small mb-2"><code>${escapeHtml(location.path)}</code></p>
                            <div class="progress" style="height: 20px;">
                                <div class="progress-bar ${progressBarClass}" role="progressbar" style="width: ${usedPercent.toFixed(2)}%;" aria-valuenow="${usedPercent.toFixed(2)}" aria-valuemin="0" aria-valuemax="100">
                                    ${usedPercent.toFixed(2)}%
                                </div>
                            </div>
                            <div class="d-flex justify-content-between text-muted small mt-1">
                                <span>Used: ${formatBytes(stat.used_bytes)}</span>
                                <span>Free: ${formatBytes(stat.free_bytes)}</span>
                                <span>Total: ${formatBytes(stat.total_bytes)}</span>
                            </div>
                        </div>`;
                }).join('');
            }
        }
        
        // Render criteria
        const criteriaTableBody = document.querySelector('#criteriaTable tbody');
        if (criteriaTableBody) {
            if (criteria.length === 0) {
                criteriaTableBody.innerHTML = `
                    <tr>
                        <td colspan="5" class="text-center text-muted">
                            No criteria configured. Files will match all criteria (move all files).
                            <br><a href="/paths/${pathId}/criteria/new" class="btn btn-sm btn-primary mt-2">
                                <i class="bi bi-plus"></i> Add Your First Criterion
                            </a>
                        </td>
                    </tr>
                `;
            } else {
                criteriaTableBody.innerHTML = criteria.map(c => `
                    <tr>
                        <td><code>${escapeHtml(c.criterion_type)}</code></td>
                        <td><code>${escapeHtml(c.operator)}</code></td>
                        <td><code>${escapeHtml(c.value)}</code></td>
                        <td>
                            <span class="badge bg-${c.enabled ? 'success' : 'secondary'}">
                                ${c.enabled ? 'Enabled' : 'Disabled'}
                            </span>
                        </td>
                        <td>
                            <div class="btn-group btn-group-sm">
                                <a href="/criteria/${c.id}/edit" class="btn btn-outline-secondary" title="Edit">
                                    <i class="bi bi-pencil"></i>
                                </a>
                                <button type="button" class="btn btn-outline-danger" onclick="deleteCriteria(${c.id}, ${pathId})" title="Delete">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </div>
                        </td>
                    </tr>
                `).join('');
            }
        }
        
        // Update edit, scan, and add criteria buttons
        const editBtn = document.getElementById('edit-path-btn');
        if (editBtn) editBtn.href = `/paths/${pathId}/edit`;
        
        const scanBtn = document.getElementById('scan-path-btn');
        if (scanBtn) {
            scanBtn.onclick = (e) => triggerScan(pathId, e.currentTarget);
            if (path.error_message) {
                scanBtn.disabled = true;
                scanBtn.title = 'Cannot scan: Path is in error state';
            }
        }
        
        const addCriteriaBtn = document.getElementById('add-criteria-btn');
        if (addCriteriaBtn) addCriteriaBtn.href = `/paths/${pathId}/criteria/new`;
        
        const viewFilesBtn = document.getElementById('view-files-btn');
        if (viewFilesBtn) viewFilesBtn.href = `/files?path_id=${pathId}`;
        
        if (contentEl) contentEl.style.display = 'block';
    } catch (error) {
        console.error('Error loading path:', error);
        if (errorEl) {
            errorEl.textContent = `Failed to load path: ${error.message}`;
            errorEl.style.display = 'block';
        }
    } finally {
        if (loadingEl) loadingEl.style.display = 'none';
    }
}

// Path actions
async function deletePath(pathId) {
    // Ask if user wants to undo operations using a modal with checkbox
    const result = await showConfirmModalWithCheckbox({
        title: 'Delete Path',
        message: 'Are you sure you want to delete this path?',
        checkboxLabel: 'Move all files back from cold storage before deleting',
        checkboxDefault: true,
        confirmText: 'Delete Path',
        dangerous: true
    });

    if (!result.confirmed) {
        return;
    }

    const undoOps = result.checked;
    
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/paths/${pathId}?undo_operations=${undoOps}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            const result = await response.json();
            let message = 'Path deleted successfully';
            if (undoOps && result.files_reversed > 0) {
                message += `. ${result.files_reversed} file(s) moved back from cold storage.`;
            }
            if (result.errors && result.errors.length > 0) {
                message += ` ${result.errors.length} error(s) occurred.`;
            }
            showNotification(message, result.errors && result.errors.length > 0 ? 'warning' : 'success');
            window.location.href = '/paths';
        } else {
            const error = await response.json();
            showNotification(error.detail || 'Failed to delete path', 'error');
        }
    } catch (error) {
        console.error('Error deleting path:', error);
        showNotification(`Error deleting path: ${error.message}`, 'error');
    }
}

async function triggerScan(pathId, buttonElement = null) {
    // Find the button element if not provided
    if (!buttonElement) {
        buttonElement = event ? event.currentTarget : document.getElementById('scan-path-btn');
    }

    // Save original button content
    const originalContent = buttonElement ? buttonElement.innerHTML : null;

    try {
        // Update button to show loading state
        if (buttonElement) {
            buttonElement.disabled = true;
            buttonElement.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Scanning...';
        }

        const response = await authenticatedFetch(`${API_BASE_URL}/paths/${pathId}/scan`, {
            method: 'POST'
        });

        if (response.ok) {
            showNotification('Scan started. Progress will appear below.', 'info');

            // Start polling for progress
            startProgressPolling(pathId);
        } else {
            const error = await response.json();
            showNotification(error.detail || 'Failed to trigger scan', 'error');

            // Restore button
            if (buttonElement && originalContent) {
                buttonElement.disabled = false;
                buttonElement.innerHTML = originalContent;
            }
        }
    } catch (error) {
        console.error('Error triggering scan:', error);
        showNotification(`Error triggering scan: ${error.message}`, 'error');

        // Restore button
        if (buttonElement && originalContent) {
            buttonElement.disabled = false;
            buttonElement.innerHTML = originalContent;
        }
    }
}

function startProgressPolling(pathId) {
    currentPathId = pathId;

    // Show progress container
    const progressContainer = document.getElementById('scan-progress-container');
    if (progressContainer) {
        progressContainer.style.display = 'block';
    }

    // Clear any existing interval
    if (progressPollingInterval) {
        clearInterval(progressPollingInterval);
    }

    // Poll every 500ms
    progressPollingInterval = setInterval(() => pollProgress(pathId), 500);

    // Also poll immediately
    pollProgress(pathId);
}

function stopProgressPolling() {
    if (progressPollingInterval) {
        clearInterval(progressPollingInterval);
        progressPollingInterval = null;
    }
}

async function pollProgress(pathId) {
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/paths/${pathId}/scan/progress`);
        if (!response.ok) {
            console.error('Failed to fetch progress');
            return;
        }

        const progress = await response.json();
        updateProgressDisplay(progress);

        // Stop polling if scan is complete or failed
        if (progress.status === 'completed' || progress.status === 'failed') {
            stopProgressPolling();

            // Hide progress after a delay
            setTimeout(() => {
                const progressContainer = document.getElementById('scan-progress-container');
                if (progressContainer) {
                    progressContainer.style.display = 'none';
                }

                // Reload page to show updated data
                if (window.location.pathname.match(/^\/paths\/\d+$/)) {
                    loadPathDetail(pathId);
                } else {
                    loadPathsList();
                }

                // Restore scan button
                const scanBtn = document.getElementById('scan-path-btn');
                if (scanBtn) {
                    scanBtn.disabled = false;
                    scanBtn.innerHTML = '<i class="bi bi-arrow-repeat"></i> Scan Now';
                }
            }, 2000);

            // Show completion message
            if (progress.status === 'completed') {
                const movedCount = progress.progress.files_moved_to_cold + progress.progress.files_moved_to_hot;
                showNotification(`Scan completed! Processed ${progress.progress.files_processed} files, moved ${movedCount} files.`, 'success');
            } else {
                showNotification('Scan failed. Check errors below.', 'error');
            }
        }
    } catch (error) {
        console.error('Error polling progress:', error);
    }
}

function updateProgressDisplay(progress) {
    // Update overall progress bar
    const progressBar = document.getElementById('scan-progress-bar');
    if (progressBar) {
        const percent = progress.progress?.percent || 0;
        progressBar.style.width = `${percent}%`;
        progressBar.setAttribute('aria-valuenow', percent);
        progressBar.textContent = `${percent}%`;
    }

    // Update progress text
    const progressText = document.getElementById('scan-progress-text');
    if (progressText) {
        const p = progress.progress || {};
        progressText.textContent = `Processing: ${p.files_processed || 0} / ${p.total_files || 0} files`;
    }

    // Update stats
    const statsContainer = document.getElementById('scan-progress-stats');
    if (statsContainer && progress.progress) {
        const p = progress.progress;
        statsContainer.innerHTML = `
            <div class="d-flex justify-content-between flex-wrap">
                <div class="me-3"><i class="bi bi-arrow-down-circle text-primary"></i> To Cold: ${p.files_moved_to_cold || 0}</div>
                <div class="me-3"><i class="bi bi-arrow-up-circle text-success"></i> To Hot: ${p.files_moved_to_hot || 0}</div>
                <div><i class="bi bi-check-circle text-secondary"></i> Skipped: ${p.files_skipped || 0}</div>
            </div>
        `;
    }

    // Update current operations
    const operationsContainer = document.getElementById('current-operations');
    if (operationsContainer && progress.current_operations) {
        if (progress.current_operations.length === 0) {
            operationsContainer.innerHTML = '<small class="text-muted">No active file operations</small>';
        } else {
            operationsContainer.innerHTML = progress.current_operations.map(op => `
                <div class="mb-2">
                    <div class="d-flex justify-content-between align-items-center mb-1">
                        <small class="text-truncate me-2" style="max-width: 70%;">${escapeHtml(op.file_name)}</small>
                        <small class="text-muted">${op.percent}%</small>
                    </div>
                    <div class="progress" style="height: 4px;">
                        <div class="progress-bar" role="progressbar" style="width: ${op.percent}%"></div>
                    </div>
                </div>
            `).join('');
        }
    }

    // Update errors
    const errorsContainer = document.getElementById('scan-errors');
    if (errorsContainer && progress.errors && progress.errors.length > 0) {
        errorsContainer.innerHTML = `
            <div class="alert alert-danger alert-sm p-2 mt-2">
                <strong>Errors:</strong>
                <ul class="mb-0 mt-1">
                    ${progress.errors.map(err => `<li><small>${escapeHtml(err)}</small></li>`).join('')}
                </ul>
            </div>
        `;
    } else if (errorsContainer) {
        errorsContainer.innerHTML = '';
    }
}

async function deleteCriteria(criteriaId, pathId) {
    const confirmed = await showConfirmModal({
        title: 'Delete Criterion',
        message: 'Are you sure you want to delete this criterion?',
        confirmText: 'Delete',
        dangerous: true
    });

    if (!confirmed) return;

    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/criteria/${criteriaId}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            showNotification('Criterion deleted successfully');
            loadPathDetail(pathId);
        } else {
            const error = await response.json();
            showNotification(error.detail || 'Failed to delete criterion', 'error');
        }
    } catch (error) {
        console.error('Error deleting criterion:', error);
        showNotification(`Error deleting criterion: ${error.message}`, 'error');
    }
}

// Form submission handlers
async function handleColdStoragePathChange(conflictData, pathId, pathData) {
    /**
     * Handle cold storage path change confirmation dialog.
     * Shows options to move files, abandon files, or cancel.
     */
    const { message, file_counts, old_path, new_path } = conflictData;

    // Create modal HTML
    const modalHtml = `
        <div class="modal fade" id="coldStorageChangeModal" tabindex="-1" aria-labelledby="coldStorageChangeModalLabel" aria-hidden="true">
            <div class="modal-dialog modal-lg">
                <div class="modal-content">
                    <div class="modal-header bg-warning text-dark">
                        <h5 class="modal-title" id="coldStorageChangeModalLabel">
                            <i class="bi bi-exclamation-triangle-fill"></i> Cold Storage Path Change Warning
                        </h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body">
                        <p class="lead">${escapeHtml(message)}</p>

                        <div class="alert alert-info">
                            <h6><i class="bi bi-info-circle"></i> File Details:</h6>
                            <ul class="mb-0">
                                <li><strong>Filesystem:</strong> ${file_counts.filesystem} files found</li>
                                <li><strong>Database Records:</strong> ${file_counts.database_records} records</li>
                                <li><strong>Inventory:</strong> ${file_counts.inventory} entries</li>
                            </ul>
                        </div>

                        <div class="alert alert-secondary">
                            <strong>Old Path:</strong> <code>${escapeHtml(old_path)}</code><br>
                            <strong>New Path:</strong> <code>${escapeHtml(new_path)}</code>
                        </div>

                        <p><strong>What would you like to do with the files in the old location?</strong></p>

                        <div class="d-grid gap-3">
                            <button type="button" class="btn btn-primary btn-lg" id="move-files-btn">
                                <i class="bi bi-arrow-right-circle"></i> Move Files
                                <br><small class="text-white-50">Physically move all files from old location to new location (Recommended)</small>
                            </button>

                            <button type="button" class="btn btn-outline-danger btn-lg" id="abandon-files-btn">
                                <i class="bi bi-trash"></i> Abandon Files
                                <br><small>Leave files in old location (they will become orphaned)</small>
                            </button>

                            <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">
                                <i class="bi bi-x-circle"></i> Cancel
                                <br><small>Don't change the cold storage path</small>
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;

    // Remove existing modal if any
    const existingModal = document.getElementById('coldStorageChangeModal');
    if (existingModal) {
        existingModal.remove();
    }

    // Add modal to page
    document.body.insertAdjacentHTML('beforeend', modalHtml);

    const modal = new bootstrap.Modal(document.getElementById('coldStorageChangeModal'));
    modal.show();

    // Handle button clicks
    return new Promise((resolve) => {
        document.getElementById('move-files-btn').addEventListener('click', async () => {
            modal.hide();
            await retryPathUpdate(pathId, pathData, 'move');
            resolve();
        });

        document.getElementById('abandon-files-btn').addEventListener('click', async () => {
            const confirmed = await showConfirmModal({
                title: 'Abandon Files',
                message: 'Are you sure you want to abandon these files? They will remain in the old location but won\'t be tracked by File Fridge.',
                confirmText: 'Abandon Files',
                dangerous: true
            });

            if (confirmed) {
                modal.hide();
                await retryPathUpdate(pathId, pathData, 'abandon');
                resolve();
            }
        });

        // Clean up modal on close
        document.getElementById('coldStorageChangeModal').addEventListener('hidden.bs.modal', () => {
            document.getElementById('coldStorageChangeModal').remove();
            resolve();
        });
    });
}

async function retryPathUpdate(pathId, pathData, migrationAction) {
    /**
     * Retry path update with migration action confirmed.
     */
    const url = `${API_BASE_URL}/paths/${pathId}?confirm_cold_storage_change=true&migration_action=${migrationAction}`;

    try {
        showNotification(`${migrationAction === 'move' ? 'Moving' : 'Abandoning'} files... This may take a moment.`, 'info');

        const response = await authenticatedFetch(url, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(pathData)
        });

        if (response.ok) {
            const path = await response.json();
            showNotification(`Path updated successfully! Files were ${migrationAction === 'move' ? 'moved to new location' : 'left in old location'}.`, 'success');
            window.location.href = `/paths/${path.id}`;
        } else {
            const error = await response.json();
            showNotification(error.detail || 'Failed to update path after confirmation', 'error');
        }
    } catch (error) {
        console.error('Error retrying path update:', error);
        showNotification(`Error updating path: ${error.message}`, 'error');
    }
}

function setupPathForm() {
    const form = document.getElementById('path-form');
    if (!form) return;
    
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const formData = new FormData(form);

        // Get selected storage location IDs from the checkboxes
        const checkedCheckboxes = document.querySelectorAll('#storage-locations-container .form-check-input:checked');
        const storageLocationIds = Array.from(checkedCheckboxes).map(cb => parseInt(cb.value));

        const pathData = {
            name: formData.get('name'),
            source_path: formData.get('source_path'),
            storage_location_ids: storageLocationIds,
            operation_type: formData.get('operation_type'),
            check_interval_seconds: parseInt(formData.get('check_interval_seconds')),
            enabled: formData.get('enabled') === 'on',
            prevent_indexing: formData.get('prevent_indexing') === 'on'
        };
        
        const pathId = form.dataset.pathId;
        const url = pathId ? `${API_BASE_URL}/paths/${pathId}` : `${API_BASE_URL}/paths`;
        const method = pathId ? 'PUT' : 'POST';
        
        try {
            const response = await authenticatedFetch(url, {
                method: method,
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(pathData)
            });

            if (response.ok) {
                const path = await response.json();
                showNotification(`Path ${pathId ? 'updated' : 'created'} successfully`);
                window.location.href = `/paths/${path.id}`;
            } else if (response.status === 409) {
                // Cold storage path change detected - show confirmation dialog
                const errorData = await response.json();
                if (errorData.detail && errorData.detail.error === 'cold_storage_path_has_files') {
                    await handleColdStoragePathChange(errorData.detail, pathId, pathData);
                } else {
                    showNotification(errorData.detail || 'Conflict error', 'error');
                }
            } else {
                const error = await response.json();
                showNotification(error.detail || `Failed to ${pathId ? 'update' : 'create'} path`, 'error');
            }
        } catch (error) {
            console.error('Error saving path:', error);
            showNotification(`Error saving path: ${error.message}`, 'error');
        }
    });
}

function setupCriteriaForm() {
    const form = document.getElementById('criteria-form');
    if (!form) return;
    
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const formData = new FormData(form);
        const criteriaData = {
            criterion_type: formData.get('criterion_type'),
            operator: formData.get('operator'),
            value: formData.get('value'),
            enabled: formData.get('enabled') === 'on'
        };
        
        const criteriaId = form.dataset.criteriaId;
        const pathId = form.dataset.pathId;
        
        if (!pathId) {
            showNotification('Path ID is required', 'error');
            return;
        }
        
        const url = criteriaId ? `${API_BASE_URL}/criteria/${criteriaId}` : `${API_BASE_URL}/criteria/path/${pathId}`;
        const method = criteriaId ? 'PUT' : 'POST';
        
        try {
            const response = await authenticatedFetch(url, {
                method: method,
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(criteriaData)
            });
            
            if (response.ok) {
                showNotification(`Criterion ${criteriaId ? 'updated' : 'created'} successfully`);
                window.location.href = `/paths/${pathId}`;
            } else {
                const error = await response.json();
                showNotification(error.detail || `Failed to ${criteriaId ? 'update' : 'create'} criterion`, 'error');
            }
        } catch (error) {
            console.error('Error saving criterion:', error);
            showNotification(`Error saving criterion: ${error.message}`, 'error');
        }
    });
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    // Check which page we're on and load appropriate data
    if (window.location.pathname === '/paths' || window.location.pathname === '/paths/') {
        loadPathsList();
    } else if (window.location.pathname.match(/^\/paths\/\d+$/)) {
        const pathId = parseInt(window.location.pathname.split('/')[2]);
        loadPathDetail(pathId);
    } else if (window.location.pathname.match(/^\/paths\/\d+\/edit$/) || window.location.pathname === '/paths/new') {
        setupPathForm();
        // If editing, load path data
        if (window.location.pathname.includes('/edit')) {
            const pathId = parseInt(window.location.pathname.split('/')[2]);
            loadPathForEdit(pathId);
            // Update form title
            document.getElementById('form-title').innerHTML = '<i class="bi bi-pencil"></i> Edit Monitored Path';
            document.getElementById('submit-text').textContent = 'Update';
        } else {
            // Load storage locations for new path form
            loadStorageLocationsCheckboxes();
        }
    } else if (window.location.pathname.match(/^\/paths\/\d+\/criteria\/new$/)) {
        setupCriteriaForm();
        // Extract path_id from URL
        const pathId = parseInt(window.location.pathname.split('/')[2]);
        loadPathForCriteriaForm(pathId);
    } else if (window.location.pathname.match(/^\/criteria\/\d+\/edit$/)) {
        setupCriteriaForm();
        // If editing, load criteria data
        const criteriaId = parseInt(window.location.pathname.split('/')[2]);
        loadCriteriaForEdit(criteriaId);
        // Update form title
        document.getElementById('form-title').innerHTML = '<i class="bi bi-pencil"></i> Edit Criteria';
        document.getElementById('submit-text').textContent = 'Update';
    }
});

// Load storage locations into checkboxes
async function loadStorageLocationsCheckboxes() {
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/storage/locations`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const locations = await response.json();

        const container = document.getElementById('storage-locations-container');
        if (container) {
            if (locations.length === 0) {
                container.innerHTML = '<p class="text-muted small m-2">No storage locations have been created yet.</p>';
                return locations;
            }
            container.innerHTML = locations.map(loc => `
                <div class="form-check">
                    <input class="form-check-input" type="checkbox" value="${loc.id}" id="storage_location_${loc.id}">
                    <label class="form-check-label" for="storage_location_${loc.id}">
                        ${escapeHtml(loc.name)} <code class="small">${escapeHtml(loc.path)}</code>
                    </label>
                </div>
            `).join('');
        }

        return locations;
    } catch (error) {
        console.error('Error loading storage locations:', error);
        showNotification(`Failed to load storage locations: ${error.message}`, 'error');
        const container = document.getElementById('storage-locations-container');
        if (container) {
            container.innerHTML = '<p class="text-danger small m-2">Failed to load storage locations.</p>';
        }
        return [];
    }
}

// Load path data for edit form
async function loadPathForEdit(pathId) {
    try {
        // Load storage locations first
        await loadStorageLocationsCheckboxes();

        const response = await authenticatedFetch(`${API_BASE_URL}/paths/${pathId}`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const path = await response.json();

        // Populate form
        document.getElementById('name').value = path.name;
        document.getElementById('source_path').value = path.source_path;
        document.getElementById('operation_type').value = path.operation_type;
        document.getElementById('check_interval_seconds').value = path.check_interval_seconds;
        document.getElementById('enabled').checked = path.enabled;
        document.getElementById('prevent_indexing').checked = path.prevent_indexing;

        // Check selected storage locations
        if (path.storage_locations) {
            const locationIds = path.storage_locations.map(loc => loc.id.toString());
            locationIds.forEach(id => {
                const checkbox = document.getElementById(`storage_location_${id}`);
                if (checkbox) {
                    checkbox.checked = true;
                }
            });
        }

        // Set form data attribute
        const form = document.getElementById('path-form');
        if (form) form.dataset.pathId = pathId;
    } catch (error) {
        console.error('Error loading path for edit:', error);
        showNotification(`Failed to load path: ${error.message}`, 'error');
    }
}

// Load path info for criteria form
async function loadPathForCriteriaForm(pathId) {
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/paths/${pathId}`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const path = await response.json();
        
        // Update path info display
        document.getElementById('path-name').textContent = path.name;
        document.getElementById('path-source').textContent = path.source_path;
        
        // Set form data attribute
        const form = document.getElementById('criteria-form');
        if (form) {
            form.dataset.pathId = pathId;
        }
        
        // Update back and cancel links
        const backLink = document.getElementById('back-link');
        const cancelLink = document.getElementById('cancel-link');
        if (backLink) backLink.href = `/paths/${pathId}`;
        if (cancelLink) cancelLink.href = `/paths/${pathId}`;
    } catch (error) {
        console.error('Error loading path for criteria form:', error);
        showNotification(`Failed to load path: ${error.message}`, 'error');
    }
}

// Load criteria data for edit form
async function loadCriteriaForEdit(criteriaId) {
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/criteria/${criteriaId}`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const criterion = await response.json();
        
        // Get path ID from criterion
        const pathId = criterion.path_id;
        
        // Load path info
        const pathResponse = await authenticatedFetch(`${API_BASE_URL}/paths/${pathId}`);
        if (pathResponse.ok) {
            const path = await pathResponse.json();
            document.getElementById('path-name').textContent = path.name;
            document.getElementById('path-source').textContent = path.source_path;
        }
        
        // Populate form
        document.getElementById('criterion_type').value = criterion.criterion_type;
        document.getElementById('operator').value = criterion.operator;
        document.getElementById('value').value = criterion.value;
        document.getElementById('enabled').checked = criterion.enabled;
        
        // Set form data attributes
        const form = document.getElementById('criteria-form');
        if (form) {
            form.dataset.criteriaId = criteriaId;
            form.dataset.pathId = pathId;
        }
        
        // Update back and cancel links
        const backLink = document.getElementById('back-link');
        const cancelLink = document.getElementById('cancel-link');
        if (backLink) backLink.href = `/paths/${pathId}`;
        if (cancelLink) cancelLink.href = `/paths/${pathId}`;
        
        // Trigger help text display
        const typeSelect = document.getElementById('criterion_type');
        if (typeSelect) typeSelect.dispatchEvent(new Event('change'));
    } catch (error) {
        console.error('Error loading criterion for edit:', error);
        showNotification(`Failed to load criterion: ${error.message}`, 'error');
    }
}

