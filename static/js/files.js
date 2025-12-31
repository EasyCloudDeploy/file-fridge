// Files browser JavaScript - client-side rendering
const API_BASE_URL = '/api/v1';

// Utility functions
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

function showFlashMessage(message, category = 'success') {
    const flashContainer = document.querySelector('main.container-fluid');
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${category} alert-dismissible fade show`;
    alertDiv.setAttribute('role', 'alert');
    alertDiv.innerHTML = `
        ${escapeHtml(message)}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    `;
    flashContainer.prepend(alertDiv);
    setTimeout(() => {
        const alert = bootstrap.Alert.getInstance(alertDiv);
        if (alert) alert.close();
    }, 5000);
}

// Load and render files list
async function loadFilesList(pathId = null, storageType = null) {
    const loadingEl = document.getElementById('files-loading');
    const contentEl = document.getElementById('files-content');
    const emptyEl = document.getElementById('no-files-message');
    const tableBody = document.querySelector('#filesTable tbody');

    if (loadingEl) loadingEl.style.display = 'block';
    if (contentEl) contentEl.style.display = 'none';
    if (emptyEl) emptyEl.style.display = 'none';
    if (tableBody) tableBody.innerHTML = '';

    try {
        let url = `${API_BASE_URL}/files?limit=100`;
        if (pathId) {
            url += `&path_id=${pathId}`;
        }
        if (storageType) {
            url += `&storage_type=${storageType}`;
        }

        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const files = await response.json();

        if (files.length === 0) {
            if (emptyEl) emptyEl.style.display = 'block';
        } else {
            if (tableBody) {
                files.forEach(file => {
                    const row = tableBody.insertRow();
                    const storageBadge = file.storage_type === 'hot'
                        ? '<span class="badge bg-success">Hot Storage</span>'
                        : '<span class="badge bg-info">Cold Storage</span>';

                    const actionButton = file.storage_type === 'cold'
                        ? `<button type="button" class="btn btn-sm btn-warning" onclick="showThawModal(${file.id}, '${escapeHtml(file.file_path)}')">
                               <i class="bi bi-fire"></i> Thaw
                           </button>`
                        : '<span class="text-muted">In Hot Storage</span>';

                    row.innerHTML = `
                        <td><code>${escapeHtml(file.file_path)}</code></td>
                        <td>${storageBadge}</td>
                        <td>${formatBytes(file.file_size)}</td>
                        <td><span class="badge bg-secondary">${escapeHtml(file.status)}</span></td>
                        <td>${formatDate(file.last_seen)}</td>
                        <td>${actionButton}</td>
                    `;
                });
            }
            if (contentEl) contentEl.style.display = 'block';
        }
    } catch (error) {
        console.error('Error loading files:', error);
        showFlashMessage(`Failed to load files: ${error.message}`, 'danger');
        if (emptyEl) emptyEl.style.display = 'block';
    } finally {
        if (loadingEl) loadingEl.style.display = 'none';
    }
}

// Load paths for filter dropdown
async function loadPathsForFilter() {
    try {
        const response = await fetch(`${API_BASE_URL}/paths`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const paths = await response.json();

        const select = document.getElementById('path_id_filter');
        if (select) {
            const urlParams = new URLSearchParams(window.location.search);
            const currentPathId = urlParams.get('path_id');

            select.innerHTML = '<option value="">All Paths</option>' +
                paths.map(p => `<option value="${p.id}" ${currentPathId == p.id ? 'selected' : ''}>${escapeHtml(p.name)}</option>`).join('');

            // Add change event listener
            select.addEventListener('change', function() {
                updateFilters();
            });
        }

        // Setup storage filter
        const storageSelect = document.getElementById('storage_filter');
        if (storageSelect) {
            const urlParams = new URLSearchParams(window.location.search);
            const currentStorage = urlParams.get('storage_type');
            if (currentStorage) {
                storageSelect.value = currentStorage;
            }

            storageSelect.addEventListener('change', function() {
                updateFilters();
            });
        }
    } catch (error) {
        console.error('Error loading paths for filter:', error);
        showFlashMessage(`Failed to load paths: ${error.message}`, 'danger');
    }
}

// Update filters and reload files
function updateFilters() {
    const pathSelect = document.getElementById('path_id_filter');
    const storageSelect = document.getElementById('storage_filter');

    const pathId = pathSelect ? pathSelect.value : null;
    const storageType = storageSelect ? storageSelect.value : null;

    loadFilesList(pathId, storageType);
}

// Show thaw modal
function showThawModal(inventoryId, filePath) {
    const modal = document.getElementById('thawModal');
    if (!modal) return;

    // Update modal content
    document.getElementById('thawFileName').textContent = filePath;

    // Set inventory ID for the confirm button
    document.getElementById('confirmThawBtn').dataset.inventoryId = inventoryId;

    // Show modal
    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();
}

// Thaw file action
async function thawFile() {
    const button = document.getElementById('confirmThawBtn');
    const inventoryId = button.dataset.inventoryId;
    const pin = document.querySelector('input[name="pin_file"]:checked').value === 'true';

    try {
        const response = await fetch(`${API_BASE_URL}/files/thaw/${inventoryId}?pin=${pin}`, {
            method: 'POST'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to thaw file');
        }

        const data = await response.json();
        showFlashMessage('File thawed successfully' + (pin ? ' and pinned' : ''));

        // Close modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('thawModal'));
        if (modal) modal.hide();

        // Reload files
        const urlParams = new URLSearchParams(window.location.search);
        const pathId = urlParams.get('path_id');
        loadFilesList(pathId);
    } catch (error) {
        console.error('Error thawing file:', error);
        showFlashMessage(`Error thawing file: ${error.message}`, 'danger');
    }
}

// Cleanup actions
async function cleanupMissingFiles() {
    const urlParams = new URLSearchParams(window.location.search);
    const pathId = urlParams.get('path_id');
    const storageType = urlParams.get('storage_type');

    try {
        let url = `${API_BASE_URL}/cleanup`;
        if (pathId) {
            url += `?path_id=${pathId}`;
        }

        const response = await fetch(url, { method: 'POST' });
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

        const data = await response.json();
        const message = `Cleanup complete: checked ${data.checked} files, removed ${data.removed} missing file records`;
        showFlashMessage(message);

        loadFilesList(pathId, storageType);
    } catch (error) {
        console.error('Error cleaning up missing files:', error);
        showFlashMessage(`Failed to cleanup missing files: ${error.message}`, 'danger');
    }
}

async function cleanupDuplicates() {
    const urlParams = new URLSearchParams(window.location.search);
    const pathId = urlParams.get('path_id');
    const storageType = urlParams.get('storage_type');

    try {
        let url = `${API_BASE_URL}/cleanup/duplicates`;
        if (pathId) {
            url += `?path_id=${pathId}`;
        }

        const response = await fetch(url, { method: 'POST' });
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

        const data = await response.json();
        const message = `Duplicate cleanup complete: checked ${data.checked} files, removed ${data.removed} duplicate records`;
        showFlashMessage(message);

        loadFilesList(pathId, storageType);
    } catch (error) {
        console.error('Error cleaning up duplicates:', error);
        showFlashMessage(`Failed to cleanup duplicates: ${error.message}`, 'danger');
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    const urlParams = new URLSearchParams(window.location.search);
    const pathId = urlParams.get('path_id');
    const storageType = urlParams.get('storage_type');

    loadFilesList(pathId, storageType);
    loadPathsForFilter();

    // Setup event handlers
    document.getElementById('confirmThawBtn').addEventListener('click', thawFile);
});

