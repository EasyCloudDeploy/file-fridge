// Files browser JavaScript - client-side rendering
const API_BASE_URL = '/api/v1';

// Pagination and filter state
let currentPage = 1;
let currentPageSize = 50;
let currentSearch = '';
let currentPathId = null;
let currentStorageType = null;
let currentSortBy = 'last_seen';
let currentSortOrder = 'desc';
let totalPages = 1;
let totalItems = 0;

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

// Notification function - uses toast notifications from app.js
function showNotification(message, type = 'success') {
    showToast(message, type);
}

// Sorting functions (server-side sorting now)
function handleSortClick(column) {
    if (currentSortBy === column) {
        // Toggle direction
        currentSortOrder = currentSortOrder === 'asc' ? 'desc' : 'asc';
    } else {
        // New column, default to descending
        currentSortBy = column;
        currentSortOrder = 'desc';
    }

    // Reset to page 1 when sorting changes
    currentPage = 1;
    loadFilesList();
}

function updateSortIndicators() {
    // Remove all sort classes
    document.querySelectorAll('th.sortable').forEach(th => {
        th.classList.remove('sort-asc', 'sort-desc');
    });

    // Add current sort class
    const currentHeader = document.querySelector(`th[data-sort="${currentSortBy}"]`);
    if (currentHeader) {
        currentHeader.classList.add(`sort-${currentSortOrder}`);
    }
}

function renderFilesTable(files) {
    const tableBody = document.querySelector('#filesTable tbody');
    if (!tableBody) return;

    tableBody.innerHTML = '';

    if (!files || files.length === 0) {
        tableBody.innerHTML = '<tr><td colspan="9" class="text-center text-muted">No files found</td></tr>';
        return;
    }

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

        // Format criteria times
        const mtime = file.file_mtime ? formatDate(file.file_mtime) : '<span class="text-muted">N/A</span>';
        const atime = file.file_atime ? formatDate(file.file_atime) : '<span class="text-muted">N/A</span>';
        const ctime = file.file_ctime ? formatDate(file.file_ctime) : '<span class="text-muted">N/A</span>';

        row.innerHTML = `
            <td><code>${escapeHtml(file.file_path)}</code></td>
            <td>${storageBadge}</td>
            <td>${formatBytes(file.file_size)}</td>
            <td><small>${mtime}</small></td>
            <td><small>${atime}</small></td>
            <td><small>${ctime}</small></td>
            <td><span class="badge bg-secondary">${escapeHtml(file.status)}</span></td>
            <td><small>${formatDate(file.last_seen)}</small></td>
            <td>${actionButton}</td>
        `;
    });
}

function renderPagination() {
    const paginationEl = document.getElementById('pagination-controls');
    if (!paginationEl) return;

    if (totalPages <= 1) {
        paginationEl.innerHTML = '';
        return;
    }

    let html = '<nav><ul class="pagination justify-content-center">';

    // Previous button
    html += `<li class="page-item ${currentPage === 1 ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="changePage(${currentPage - 1}); return false;">Previous</a>
    </li>`;

    // Page numbers
    const maxPagesToShow = 5;
    let startPage = Math.max(1, currentPage - Math.floor(maxPagesToShow / 2));
    let endPage = Math.min(totalPages, startPage + maxPagesToShow - 1);

    if (endPage - startPage < maxPagesToShow - 1) {
        startPage = Math.max(1, endPage - maxPagesToShow + 1);
    }

    if (startPage > 1) {
        html += `<li class="page-item"><a class="page-link" href="#" onclick="changePage(1); return false;">1</a></li>`;
        if (startPage > 2) {
            html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
        }
    }

    for (let i = startPage; i <= endPage; i++) {
        html += `<li class="page-item ${i === currentPage ? 'active' : ''}">
            <a class="page-link" href="#" onclick="changePage(${i}); return false;">${i}</a>
        </li>`;
    }

    if (endPage < totalPages) {
        if (endPage < totalPages - 1) {
            html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
        }
        html += `<li class="page-item"><a class="page-link" href="#" onclick="changePage(${totalPages}); return false;">${totalPages}</a></li>`;
    }

    // Next button
    html += `<li class="page-item ${currentPage === totalPages ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="changePage(${currentPage + 1}); return false;">Next</a>
    </li>`;

    html += '</ul></nav>';

    // Add page info
    const start = (currentPage - 1) * currentPageSize + 1;
    const end = Math.min(currentPage * currentPageSize, totalItems);
    html += `<p class="text-center text-muted">Showing ${start}-${end} of ${totalItems} files</p>`;

    paginationEl.innerHTML = html;
}

function changePage(newPage) {
    if (newPage < 1 || newPage > totalPages || newPage === currentPage) return;
    currentPage = newPage;
    loadFilesList();
}

// Load and render files list
async function loadFilesList() {
    const loadingEl = document.getElementById('files-loading');
    const contentEl = document.getElementById('files-content');
    const emptyEl = document.getElementById('no-files-message');
    const tableBody = document.querySelector('#filesTable tbody');

    if (loadingEl) loadingEl.style.display = 'block';
    if (contentEl) contentEl.style.display = 'none';
    if (emptyEl) emptyEl.style.display = 'none';
    if (tableBody) tableBody.innerHTML = '';

    try {
        // Build URL with all parameters
        const params = new URLSearchParams({
            page: currentPage,
            page_size: currentPageSize,
            sort_by: currentSortBy,
            sort_order: currentSortOrder
        });

        if (currentPathId) {
            params.append('path_id', currentPathId);
        }
        if (currentStorageType) {
            params.append('storage_type', currentStorageType);
        }
        if (currentSearch) {
            params.append('search', currentSearch);
        }

        const url = `${API_BASE_URL}/files?${params.toString()}`;
        const response = await fetch(url);

        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

        const data = await response.json();

        // Update pagination state
        totalPages = data.total_pages;
        totalItems = data.total;

        // Render table and pagination
        renderFilesTable(data.items);
        renderPagination();
        updateSortIndicators();

        if (data.items.length === 0) {
            if (emptyEl) emptyEl.style.display = 'block';
        } else {
            if (contentEl) contentEl.style.display = 'block';
        }
    } catch (error) {
        console.error('Error loading files:', error);
        showNotification(`Failed to load files: ${error.message}`, 'error');
        if (emptyEl) emptyEl.style.display = 'block';
    } finally {
        if (loadingEl) loadingEl.style.display = 'none';
    }
}

// Search function
function performSearch() {
    const searchInput = document.getElementById('search_input');
    currentSearch = searchInput ? searchInput.value.trim() : '';
    currentPage = 1; // Reset to first page on search
    loadFilesList();
}

// Clear search
function clearSearch() {
    const searchInput = document.getElementById('search_input');
    if (searchInput) searchInput.value = '';
    currentSearch = '';
    currentPage = 1;
    loadFilesList();
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
        showNotification(`Failed to load paths: ${error.message}`, 'error');
    }
}

// Update filters and reload files
function updateFilters() {
    const pathSelect = document.getElementById('path_id_filter');
    const storageSelect = document.getElementById('storage_filter');

    currentPathId = pathSelect && pathSelect.value ? parseInt(pathSelect.value) : null;
    currentStorageType = storageSelect && storageSelect.value ? storageSelect.value : null;

    currentPage = 1; // Reset to first page on filter change
    loadFilesList();
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
        showNotification('File thawed successfully' + (pin ? ' and pinned' : ''));

        // Close modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('thawModal'));
        if (modal) modal.hide();

        // Reload files
        loadFilesList();
    } catch (error) {
        console.error('Error thawing file:', error);
        showNotification(`Error thawing file: ${error.message}`, 'error');
    }
}

// Cleanup actions
async function cleanupMissingFiles() {
    try {
        let url = `${API_BASE_URL}/cleanup`;
        if (currentPathId) {
            url += `?path_id=${currentPathId}`;
        }

        const response = await fetch(url, { method: 'POST' });
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

        const data = await response.json();
        const message = `Cleanup complete: checked ${data.checked} files, removed ${data.removed} missing file records`;
        showNotification(message);

        loadFilesList();
    } catch (error) {
        console.error('Error cleaning up missing files:', error);
        showNotification(`Failed to cleanup missing files: ${error.message}`, 'error');
    }
}

async function cleanupDuplicates() {
    try {
        let url = `${API_BASE_URL}/cleanup/duplicates`;
        if (currentPathId) {
            url += `?path_id=${currentPathId}`;
        }

        const response = await fetch(url, { method: 'POST' });
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

        const data = await response.json();
        const message = `Duplicate cleanup complete: checked ${data.checked} files, removed ${data.removed} duplicate records`;
        showNotification(message);

        loadFilesList();
    } catch (error) {
        console.error('Error cleaning up duplicates:', error);
        showNotification(`Failed to cleanup duplicates: ${error.message}`, 'error');
    }
}

// Setup sortable column click handlers
function setupSortHandlers() {
    document.querySelectorAll('th.sortable').forEach(header => {
        header.addEventListener('click', function() {
            const column = this.getAttribute('data-sort');
            handleSortClick(column);
        });
    });
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    const urlParams = new URLSearchParams(window.location.search);
    currentPathId = urlParams.get('path_id') ? parseInt(urlParams.get('path_id')) : null;
    currentStorageType = urlParams.get('storage_type') || null;

    loadFilesList();
    loadPathsForFilter();
    setupSortHandlers();

    // Setup event handlers
    const confirmBtn = document.getElementById('confirmThawBtn');
    if (confirmBtn) {
        confirmBtn.addEventListener('click', thawFile);
    }

    // Setup search
    const searchInput = document.getElementById('search_input');
    if (searchInput) {
        searchInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                performSearch();
            }
        });
    }

    const searchBtn = document.getElementById('search_btn');
    if (searchBtn) {
        searchBtn.addEventListener('click', performSearch);
    }

    const clearBtn = document.getElementById('clear_search_btn');
    if (clearBtn) {
        clearBtn.addEventListener('click', clearSearch);
    }
});

