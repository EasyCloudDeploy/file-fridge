// Files browser JavaScript - client-side rendering
const API_BASE_URL = '/api/v1';

// Pagination and filter state
let currentPage = 1;
let currentPageSize = 50;
let currentSearch = '';
let currentPathId = null;
let currentStorageType = null;
let currentTagIds = [];
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
        tableBody.innerHTML = '<tr><td colspan="11" class="text-center text-muted">No files found</td></tr>';
        return;
    }

    files.forEach(file => {
        const row = tableBody.insertRow();
        const storageBadge = file.storage_type === 'hot'
            ? '<span class="badge bg-success">Hot</span>'
            : '<span class="badge bg-info">Cold</span>';

        // Storage location display for cold storage files
        let storageLocationHtml = '<span class="text-muted">-</span>';
        if (file.storage_type === 'cold' && file.storage_location) {
            if (file.storage_location.available === false) {
                // Storage is unavailable (ejected/disconnected)
                storageLocationHtml = `
                    <span class="badge bg-danger" title="Storage unavailable - drive may be ejected">
                        <i class="bi bi-exclamation-triangle"></i> ${escapeHtml(file.storage_location.name)}
                    </span>`;
            } else {
                storageLocationHtml = `<span class="badge bg-secondary">${escapeHtml(file.storage_location.name)}</span>`;
            }
        }

        // Check if file is migrating
        const isMigrating = file.status === 'migrating';
        // Check if storage is unavailable
        const storageUnavailable = file.storage_type === 'cold' &&
            file.storage_location && file.storage_location.available === false;

        let actionButton;
        if (isMigrating) {
            // Show migrating indicator instead of action buttons
            actionButton = `
                <span class="text-warning">
                    <span class="spinner-border spinner-border-sm" role="status"></span>
                    Migrating...
                </span>`;
        } else if (storageUnavailable) {
            // Storage is ejected/unavailable - disable actions
            actionButton = `
                <span class="text-danger" title="Storage unavailable - reconnect drive to perform actions">
                    <i class="bi bi-hdd-network"></i> Offline
                </span>`;
        } else if (file.storage_type === 'cold') {
            actionButton = `
                <div class="btn-group btn-group-sm" role="group">
                    <button type="button" class="btn btn-warning" onclick="showThawModal(${file.id}, '${escapeHtml(file.file_path)}')" title="Move file back to hot storage">
                        <i class="bi bi-fire"></i> Thaw
                    </button>
                    <button type="button" class="btn btn-outline-primary" onclick="showRelocateModal(${file.id}, '${escapeHtml(file.file_path)}')" title="Move to another cold storage location">
                        <i class="bi bi-arrow-right-circle"></i> Relocate
                    </button>
                </div>`;
        } else {
            actionButton = '<span class="text-muted">-</span>';
        }

        // Format criteria times
        const mtime = file.file_mtime ? formatDate(file.file_mtime) : '<span class="text-muted">N/A</span>';
        const atime = file.file_atime ? formatDate(file.file_atime) : '<span class="text-muted">N/A</span>';
        const ctime = file.file_ctime ? formatDate(file.file_ctime) : '<span class="text-muted">N/A</span>';

        // Render tags
        let tagsHtml = '';
        if (file.tags && file.tags.length > 0) {
            tagsHtml = file.tags.map(ft => {
                const color = ft.tag && ft.tag.color ? ft.tag.color : '#6c757d';
                const name = ft.tag && ft.tag.name ? ft.tag.name : 'Unknown';
                return `<span class="badge me-1" style="background-color: ${color};">${escapeHtml(name)}</span>`;
            }).join('');
        }
        tagsHtml += `<button type="button" class="btn btn-sm btn-outline-primary" onclick="showManageTagsModal(${file.id}, '${escapeHtml(file.file_path)}')" title="Manage tags">
            <i class="bi bi-tags"></i>
        </button>`;

        // Status badge with appropriate color
        let statusBadgeClass = 'bg-secondary';
        if (file.status === 'active') {
            statusBadgeClass = 'bg-success';
        } else if (file.status === 'migrating') {
            statusBadgeClass = 'bg-warning text-dark';
        } else if (file.status === 'missing' || file.status === 'deleted') {
            statusBadgeClass = 'bg-danger';
        }

        row.innerHTML = `
            <td><code>${escapeHtml(file.file_path)}</code></td>
            <td>${storageBadge}</td>
            <td>${storageLocationHtml}</td>
            <td>${formatBytes(file.file_size)}</td>
            <td><small>${mtime}</small></td>
            <td><small>${atime}</small></td>
            <td><small>${ctime}</small></td>
            <td><span class="badge ${statusBadgeClass}">${escapeHtml(file.status)}</span></td>
            <td><small>${formatDate(file.last_seen)}</small></td>
            <td>${tagsHtml}</td>
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
        if (currentTagIds.length > 0) {
            params.append('tag_ids', currentTagIds.join(','));
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
    const tagSelect = document.getElementById('tag_filter');

    currentPathId = pathSelect && pathSelect.value ? parseInt(pathSelect.value) : null;
    currentStorageType = storageSelect && storageSelect.value ? storageSelect.value : null;

    // Get selected tag IDs from multi-select
    if (tagSelect) {
        const selectedOptions = Array.from(tagSelect.selectedOptions);
        currentTagIds = selectedOptions
            .map(opt => opt.value)
            .filter(val => val !== '') // Filter out "All Tags" option
            .map(val => parseInt(val));
    }

    currentPage = 1; // Reset to first page on filter change
    loadFilesList();
}

// Load tags for filter dropdown
async function loadTagsForFilter() {
    try {
        const response = await fetch(`${API_BASE_URL}/tags`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const tags = await response.json();

        const select = document.getElementById('tag_filter');
        if (select) {
            select.innerHTML = '<option value="">All Tags</option>' +
                tags.map(tag => {
                    const color = tag.color || '#6c757d';
                    return `<option value="${tag.id}" style="background-color: ${color}20;">${escapeHtml(tag.name)} (${tag.file_count})</option>`;
                }).join('');

            // Add change event listener
            select.addEventListener('change', function() {
                updateFilters();
            });
        }
    } catch (error) {
        console.error('Error loading tags for filter:', error);
        showNotification(`Failed to load tags: ${error.message}`, 'error');
    }
}

// Clear tag filter
function clearTagFilter() {
    const tagSelect = document.getElementById('tag_filter');
    if (tagSelect) {
        tagSelect.selectedIndex = 0;
        // Clear all selections
        Array.from(tagSelect.options).forEach(opt => opt.selected = false);
    }
    currentTagIds = [];
    currentPage = 1;
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

// Tag Management Functions
let currentFileId = null;
let allAvailableTags = [];
let manageTagsModal = null;

// Relocate Modal Management
let relocateModal = null;
let currentRelocateInventoryId = null;

// Load all available tags
async function loadAvailableTags() {
    try {
        const response = await fetch(`${API_BASE_URL}/tags`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        allAvailableTags = await response.json();
        return allAvailableTags;
    } catch (error) {
        console.error('Error loading tags:', error);
        showNotification(`Failed to load tags: ${error.message}`, 'error');
        return [];
    }
}

// Show manage tags modal
async function showManageTagsModal(fileId, filePath) {
    currentFileId = fileId;

    const modal = document.getElementById('manageTagsModal');
    if (!modal) return;

    // Update modal content
    document.getElementById('tagFileName').textContent = filePath;

    // Load available tags if not already loaded
    if (allAvailableTags.length === 0) {
        await loadAvailableTags();
    }

    // Populate tag select dropdown
    const selectEl = document.getElementById('addTagSelect');
    if (selectEl) {
        selectEl.innerHTML = '<option value="">Select a tag...</option>' +
            allAvailableTags.map(tag => `<option value="${tag.id}">${escapeHtml(tag.name)}</option>`).join('');
    }

    // Load current tags for this file
    await loadFileTags(fileId);

    // Show modal
    if (!manageTagsModal) {
        manageTagsModal = new bootstrap.Modal(modal);
    }
    manageTagsModal.show();
}

// Load tags for a specific file
async function loadFileTags(fileId) {
    const currentTagsEl = document.getElementById('currentTags');
    if (!currentTagsEl) return;

    currentTagsEl.innerHTML = '<span class="text-muted">Loading...</span>';

    try {
        const response = await fetch(`${API_BASE_URL}/tags/files/${fileId}/tags`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

        const fileTags = await response.json();

        if (fileTags.length === 0) {
            currentTagsEl.innerHTML = '<span class="text-muted">No tags assigned</span>';
        } else {
            currentTagsEl.innerHTML = fileTags.map(ft => {
                const color = ft.tag && ft.tag.color ? ft.tag.color : '#6c757d';
                const name = ft.tag && ft.tag.name ? ft.tag.name : 'Unknown';
                const tagId = ft.tag_id;
                return `
                    <span class="badge me-2 mb-2" style="background-color: ${color};">
                        ${escapeHtml(name)}
                        <button type="button" class="btn-close btn-close-white ms-2" style="font-size: 0.6rem;"
                                onclick="removeTag(${fileId}, ${tagId})" title="Remove tag"></button>
                    </span>
                `;
            }).join('');
        }
    } catch (error) {
        console.error('Error loading file tags:', error);
        currentTagsEl.innerHTML = '<span class="text-danger">Error loading tags</span>';
        showNotification(`Failed to load file tags: ${error.message}`, 'error');
    }
}

// Add tag to file
async function addTagToFile() {
    const selectEl = document.getElementById('addTagSelect');
    const tagId = selectEl ? parseInt(selectEl.value) : null;

    if (!tagId || !currentFileId) return;

    const errorEl = document.getElementById('tagError');
    const successEl = document.getElementById('tagSuccess');

    // Clear previous messages
    if (errorEl) {
        errorEl.textContent = '';
        errorEl.classList.add('d-none');
    }
    if (successEl) {
        successEl.textContent = '';
        successEl.classList.add('d-none');
    }

    try {
        const response = await fetch(`${API_BASE_URL}/tags/files/${currentFileId}/tags`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                tag_id: tagId,
                tagged_by: 'user'
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to add tag');
        }

        // Show success message
        if (successEl) {
            successEl.textContent = 'Tag added successfully';
            successEl.classList.remove('d-none');
        }

        // Reset select
        if (selectEl) selectEl.value = '';

        // Reload tags for this file
        await loadFileTags(currentFileId);

        // Reload files list to update the table
        loadFilesList();
    } catch (error) {
        console.error('Error adding tag:', error);
        if (errorEl) {
            errorEl.textContent = error.message;
            errorEl.classList.remove('d-none');
        }
    }
}

// Remove tag from file
async function removeTag(fileId, tagId) {
    const errorEl = document.getElementById('tagError');
    const successEl = document.getElementById('tagSuccess');

    // Clear previous messages
    if (errorEl) {
        errorEl.textContent = '';
        errorEl.classList.add('d-none');
    }
    if (successEl) {
        successEl.textContent = '';
        successEl.classList.add('d-none');
    }

    try {
        const response = await fetch(`${API_BASE_URL}/tags/files/${fileId}/tags/${tagId}`, {
            method: 'DELETE'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to remove tag');
        }

        // Show success message
        if (successEl) {
            successEl.textContent = 'Tag removed successfully';
            successEl.classList.remove('d-none');
        }

        // Reload tags for this file
        await loadFileTags(fileId);

        // Reload files list to update the table
        loadFilesList();
    } catch (error) {
        console.error('Error removing tag:', error);
        if (errorEl) {
            errorEl.textContent = error.message;
            errorEl.classList.remove('d-none');
        }
    }
}

// Relocate Functions

// Show relocate modal
async function showRelocateModal(inventoryId, filePath) {
    currentRelocateInventoryId = inventoryId;

    const modal = document.getElementById('relocateModal');
    if (!modal) return;

    // Reset modal state
    document.getElementById('relocateFileName').textContent = filePath;
    document.getElementById('relocateCurrentLocation').innerHTML = '<span class="text-muted">Loading...</span>';
    document.getElementById('relocateTargetSelect').innerHTML = '<option value="">Loading locations...</option>';
    document.getElementById('relocateTargetSelect').disabled = true;
    document.getElementById('confirmRelocateBtn').disabled = true;

    const errorEl = document.getElementById('relocateError');
    const noOptionsEl = document.getElementById('relocateNoOptions');
    if (errorEl) {
        errorEl.textContent = '';
        errorEl.classList.add('d-none');
    }
    if (noOptionsEl) {
        noOptionsEl.classList.add('d-none');
    }

    // Show modal
    if (!relocateModal) {
        relocateModal = new bootstrap.Modal(modal);
    }
    relocateModal.show();

    // Load relocate options
    await loadRelocateOptions(inventoryId);
}

// Load relocate options for a file
async function loadRelocateOptions(inventoryId) {
    const selectEl = document.getElementById('relocateTargetSelect');
    const currentLocationEl = document.getElementById('relocateCurrentLocation');
    const noOptionsEl = document.getElementById('relocateNoOptions');
    const errorEl = document.getElementById('relocateError');
    const confirmBtn = document.getElementById('confirmRelocateBtn');

    try {
        const response = await fetch(`${API_BASE_URL}/files/relocate/${inventoryId}/options`);
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to load options');
        }

        const data = await response.json();

        // Show current location
        const currentLocation = data.available_locations.find(loc => loc.is_current);
        if (currentLocation) {
            currentLocationEl.innerHTML = `<span class="badge bg-info me-2">${escapeHtml(currentLocation.name)}</span><small class="text-muted">${escapeHtml(currentLocation.path)}</small>`;
        } else {
            currentLocationEl.innerHTML = '<span class="text-muted">Unknown</span>';
        }

        // Filter to get only non-current locations
        const targetLocations = data.available_locations.filter(loc => !loc.is_current);

        if (!data.can_relocate || targetLocations.length === 0) {
            // No other locations available
            selectEl.innerHTML = '<option value="">No other locations available</option>';
            selectEl.disabled = true;
            confirmBtn.disabled = true;
            if (noOptionsEl) noOptionsEl.classList.remove('d-none');
        } else {
            // Populate target locations
            selectEl.innerHTML = '<option value="">Select target location...</option>' +
                targetLocations.map(loc =>
                    `<option value="${loc.id}">${escapeHtml(loc.name)} (${escapeHtml(loc.path)})</option>`
                ).join('');
            selectEl.disabled = false;
            if (noOptionsEl) noOptionsEl.classList.add('d-none');
        }
    } catch (error) {
        console.error('Error loading relocate options:', error);
        if (errorEl) {
            errorEl.textContent = error.message;
            errorEl.classList.remove('d-none');
        }
        selectEl.innerHTML = '<option value="">Error loading locations</option>';
        selectEl.disabled = true;
        confirmBtn.disabled = true;
    }
}

// Handle target location selection
function onRelocateTargetChange() {
    const selectEl = document.getElementById('relocateTargetSelect');
    const confirmBtn = document.getElementById('confirmRelocateBtn');

    if (confirmBtn) {
        confirmBtn.disabled = !selectEl || !selectEl.value;
    }
}

// Relocate file action
async function relocateFile() {
    const selectEl = document.getElementById('relocateTargetSelect');
    const targetLocationId = selectEl ? parseInt(selectEl.value) : null;
    const confirmBtn = document.getElementById('confirmRelocateBtn');
    const errorEl = document.getElementById('relocateError');

    if (!targetLocationId || !currentRelocateInventoryId) return;

    // Disable button during operation
    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status"></span> Starting...';
    }

    // Clear previous error
    if (errorEl) {
        errorEl.textContent = '';
        errorEl.classList.add('d-none');
    }

    try {
        const response = await fetch(`${API_BASE_URL}/files/relocate/${currentRelocateInventoryId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                target_storage_location_id: targetLocationId
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to start relocation');
        }

        const data = await response.json();

        // Task was created successfully - it runs in the background
        showNotification(`Relocation started: moving file to ${data.target_location.name}. This will complete in the background.`);

        // Close modal
        if (relocateModal) relocateModal.hide();

        // Reload files to show updated state
        loadFilesList();
    } catch (error) {
        console.error('Error starting relocation:', error);
        showNotification(`Error starting relocation: ${error.message}`, 'error');
        if (errorEl) {
            errorEl.textContent = error.message;
            errorEl.classList.remove('d-none');
        }
    } finally {
        // Reset button
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.innerHTML = '<i class="bi bi-arrow-right-circle"></i> Relocate File';
        }
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    const urlParams = new URLSearchParams(window.location.search);
    currentPathId = urlParams.get('path_id') ? parseInt(urlParams.get('path_id')) : null;
    currentStorageType = urlParams.get('storage_type') || null;

    loadFilesList();
    loadPathsForFilter();
    loadTagsForFilter();
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

    // Setup tag management
    const addTagSelect = document.getElementById('addTagSelect');
    if (addTagSelect) {
        addTagSelect.addEventListener('change', function() {
            const addTagBtn = document.getElementById('addTagBtn');
            if (addTagBtn) {
                addTagBtn.disabled = !this.value;
            }
        });
    }

    const addTagBtn = document.getElementById('addTagBtn');
    if (addTagBtn) {
        addTagBtn.addEventListener('click', addTagToFile);
    }

    // Setup clear tag filter
    const clearTagFilterBtn = document.getElementById('clear_tag_filter_btn');
    if (clearTagFilterBtn) {
        clearTagFilterBtn.addEventListener('click', clearTagFilter);
    }

    // Setup relocate modal
    const relocateTargetSelect = document.getElementById('relocateTargetSelect');
    if (relocateTargetSelect) {
        relocateTargetSelect.addEventListener('change', onRelocateTargetChange);
    }

    const confirmRelocateBtn = document.getElementById('confirmRelocateBtn');
    if (confirmRelocateBtn) {
        confirmRelocateBtn.addEventListener('click', relocateFile);
    }
});

