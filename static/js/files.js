// File browser JavaScript - uses API to load data and handle actions
document.addEventListener('DOMContentLoaded', function() {
    const pathId = new URLSearchParams(window.location.search).get('path_id');
    loadFiles(pathId);
    setupEventHandlers();
});

function loadFiles(pathId) {
    let url = '/api/v1/files?limit=100';
    if (pathId) {
        url += `&path_id=${pathId}`;
    }
    
    fetch(url)
        .then(response => response.json())
        .then(data => {
            renderFiles(data);
            loadPaths();
        })
        .catch(error => {
            console.error('Error loading files:', error);
            showError('Failed to load files');
        });
}

function loadPaths() {
    fetch('/api/v1/paths')
        .then(response => response.json())
        .then(paths => {
            const select = document.getElementById('path_id');
            if (select) {
                const currentPathId = new URLSearchParams(window.location.search).get('path_id');
                select.innerHTML = '<option value="">All Paths</option>' +
                    paths.map(p => `<option value="${p.id}" ${currentPathId == p.id ? 'selected' : ''}>${p.name}</option>`).join('');
            }
        })
        .catch(error => {
            console.error('Error loading paths:', error);
        });
}

function renderFiles(files) {
    const tbody = document.querySelector('#filesTable tbody');
    if (!tbody) return;
    
    if (files.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center">No files have been moved yet.</td></tr>';
        return;
    }
    
    tbody.innerHTML = files.map(file => `
        <tr>
            <td><code>${escapeHtml(file.original_path)}</code></td>
            <td><code>${escapeHtml(file.cold_storage_path)}</code></td>
            <td>${formatBytes(file.file_size)}</td>
            <td><span class="badge bg-info">${file.operation_type}</span></td>
            <td>${formatDate(file.moved_at)}</td>
            <td>
                <button type="button" class="btn btn-sm btn-warning" data-bs-toggle="modal" data-bs-target="#thawModal${file.id}" data-file-id="${file.id}">
                    <i class="bi bi-fire"></i> Thaw
                </button>
            </td>
        </tr>
    `).join('');
    
    // Add modals for each file
    files.forEach(file => {
        addThawModal(file);
    });
}

function addThawModal(file) {
    const modalHtml = `
        <div class="modal fade" id="thawModal${file.id}" tabindex="-1">
            <div class="modal-dialog">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">Thaw File</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <form id="thawForm${file.id}">
                        <div class="modal-body">
                            <p>Move this file back from cold storage to hot storage?</p>
                            <p><strong>File:</strong> <code>${escapeHtml(file.original_path)}</code></p>
                            <p><strong>Cold Storage:</strong> <code>${escapeHtml(file.cold_storage_path)}</code></p>
                            
                            <div class="mb-3">
                                <div class="form-check">
                                    <input class="form-check-input" type="radio" name="pin" id="pinTemp${file.id}" value="false" checked>
                                    <label class="form-check-label" for="pinTemp${file.id}">
                                        <strong>Temporary</strong> - Move back, but file may be moved again on next scan if it matches criteria
                                    </label>
                                </div>
                            </div>
                            <div class="mb-3">
                                <div class="form-check">
                                    <input class="form-check-input" type="radio" name="pin" id="pinPermanent${file.id}" value="true">
                                    <label class="form-check-label" for="pinPermanent${file.id}">
                                        <strong>Pinned</strong> - Move back and exclude from future scans
                                    </label>
                                </div>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                            <button type="submit" class="btn btn-warning">
                                <i class="bi bi-fire"></i> Thaw File
                            </button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    `;
    
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    document.getElementById(`thawForm${file.id}`).addEventListener('submit', function(e) {
        e.preventDefault();
        const pin = this.querySelector('input[name="pin"]:checked').value === 'true';
        thawFile(file.id, pin);
    });
}

function thawFile(fileId, pin) {
    fetch(`/api/v1/files/thaw/${fileId}?pin=${pin}`, {
        method: 'POST'
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(err => { throw new Error(err.detail || 'Failed to thaw file'); });
        }
        return response.json();
    })
    .then(data => {
        showMessage('File thawed successfully' + (pin ? ' and pinned' : ''));
        // Close modal
        const modal = bootstrap.Modal.getInstance(document.getElementById(`thawModal${fileId}`));
        if (modal) modal.hide();
        // Reload files
        const pathId = new URLSearchParams(window.location.search).get('path_id');
        loadFiles(pathId);
    })
    .catch(error => {
        showError(error.message);
    });
}

function setupEventHandlers() {
    // Cleanup buttons
    document.querySelectorAll('form[action="/cleanup"]').forEach(form => {
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            const pathId = new URLSearchParams(window.location.search).get('path_id');
            cleanupMissing(pathId);
        });
    });
    
    document.querySelectorAll('form[action="/cleanup/duplicates"]').forEach(form => {
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            const pathId = new URLSearchParams(window.location.search).get('path_id');
            cleanupDuplicates(pathId);
        });
    });
}

function cleanupMissing(pathId) {
    let url = '/api/v1/cleanup';
    if (pathId) {
        url += `?path_id=${pathId}`;
    }
    
    fetch(url, { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            const message = `Cleanup complete: checked ${data.checked} files, removed ${data.removed} missing file records`;
            showMessage(message);
            loadFiles(pathId);
        })
        .catch(error => {
            showError('Failed to cleanup missing files: ' + error.message);
        });
}

function cleanupDuplicates(pathId) {
    let url = '/api/v1/cleanup/duplicates';
    if (pathId) {
        url += `?path_id=${pathId}`;
    }
    
    fetch(url, { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            const message = `Duplicate cleanup complete: checked ${data.checked} files, removed ${data.removed} duplicate records`;
            showMessage(message);
            loadFiles(pathId);
        })
        .catch(error => {
            showError('Failed to cleanup duplicates: ' + error.message);
        });
}

function showMessage(message) {
    const alertDiv = document.createElement('div');
    alertDiv.className = 'alert alert-success alert-dismissible fade show';
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

function showError(error) {
    const alertDiv = document.createElement('div');
    alertDiv.className = 'alert alert-danger alert-dismissible fade show';
    alertDiv.innerHTML = `
        ${escapeHtml(error)}
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

