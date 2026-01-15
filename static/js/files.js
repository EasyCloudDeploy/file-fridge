// Files browser JavaScript - client-side rendering with virtual scrolling and pagination
const API_BASE_URL = '/api/v1';

// Filter state
let currentSearch = '';
let currentPathId = null;
let currentStorageType = null;
let currentFileStatus = null;
let currentTagIds = [];
let currentSortBy = 'last_seen';
let currentSortOrder = 'desc';
let totalItems = 0;

// Pagination state
let nextCursor = null;
let hasMoreData = false;
let isLoadingMore = false;
let pageSize = 200; // Load 200 files per page

// Streaming state
let currentAbortController = null;

// Virtual table instance
let virtualTable = null;

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

// Sorting functions (server-side sorting)
function handleSortClick(column) {
    if (currentSortBy === column) {
        // Toggle direction
        currentSortOrder = currentSortOrder === 'asc' ? 'desc' : 'asc';
    } else {
        // New column, default to descending
        currentSortBy = column;
        currentSortOrder = 'desc';
    }

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

/**
 * Create a table row element for a file (used by VirtualTable)
 */
function createFileRow(file, index) {
    const row = document.createElement('tr');
    row.className = 'virtual-table-row';
    row.dataset.fileId = file.id;
    row.dataset.filePath = file.file_path;

    const storageBadge = file.storage_type === 'hot'
        ? '<span class="badge bg-success">Hot</span>'
        : '<span class="badge bg-info">Cold</span>';

    // Storage location display for cold storage files
    let storageLocationHtml = '<span class="text-muted">-</span>';
    if (file.storage_type === 'cold' && file.storage_location) {
        if (file.storage_location.available === false) {
            storageLocationHtml = `
                <span class="badge bg-danger" title="Storage unavailable - drive may be ejected">
                    <i class="bi bi-exclamation-triangle"></i> ${escapeHtml(file.storage_location.name)}
                </span>`;
        } else {
            storageLocationHtml = `<span class="badge bg-secondary">${escapeHtml(file.storage_location.name)}</span>`;
        }
    }

    const isMigrating = file.status === 'migrating';
    const storageUnavailable = file.storage_type === 'cold' &&
        file.storage_location && file.storage_location.available === false;

    let actionButton;
    if (isMigrating) {
        actionButton = `
            <span class="text-warning">
                <span class="spinner-border spinner-border-sm" role="status"></span>
                <span class="d-none d-sm-inline">Migrating...</span>
            </span>`;
    } else if (storageUnavailable) {
        actionButton = `
            <span class="text-danger" title="Storage unavailable - reconnect drive to perform actions">
                <i class="bi bi-hdd-network"></i><span class="d-none d-sm-inline"> Offline</span>
            </span>`;
    } else if (file.storage_type === 'cold') {
        actionButton = `
            <div class="btn-group btn-group-sm" role="group">
                <button type="button" class="btn btn-warning" data-action="thaw" title="Move file back to hot storage">
                    <i class="bi bi-fire"></i><span class="d-none d-lg-inline"> Thaw</span>
                </button>
                <button type="button" class="btn btn-outline-primary" data-action="relocate" title="Move to another cold storage location">
                    <i class="bi bi-arrow-right-circle"></i><span class="d-none d-xl-inline"> Relocate</span>
                </button>
            </div>`;
    } else {
        // Hot storage files - show freeze button
        actionButton = `
            <button type="button" class="btn btn-sm btn-info" data-action="freeze" title="Send to cold storage">
                <i class="bi bi-snow"></i><span class="d-none d-lg-inline"> Fridge</span>
            </button>`;
    }

    const mtime = file.file_mtime ? formatDate(file.file_mtime) : '<span class="text-muted">N/A</span>';
    const atime = file.file_atime ? formatDate(file.file_atime) : '<span class="text-muted">N/A</span>';
    const ctime = file.file_ctime ? formatDate(file.file_ctime) : '<span class="text-muted">N/A</span>';

    let tagsHtml = '';
    if (file.tags && file.tags.length > 0) {
        tagsHtml = file.tags.map(ft => {
            const color = ft.tag && ft.tag.color ? ft.tag.color : '#6c757d';
            const name = ft.tag && ft.tag.name ? ft.tag.name : 'Unknown';
            return `<span class="badge me-1" style="background-color: ${color};">${escapeHtml(name)}</span>`;
        }).join('');
    }
    tagsHtml += `<button type="button" class="btn btn-sm btn-outline-primary" data-action="manageTags" title="Manage tags">
        <i class="bi bi-tags"></i>
    </button>`;

    let statusBadgeClass = 'bg-secondary';
    if (file.status === 'active') {
        statusBadgeClass = 'bg-success';
    } else if (file.status === 'migrating') {
        statusBadgeClass = 'bg-warning text-dark';
    } else if (file.status === 'missing' || file.status === 'deleted') {
        statusBadgeClass = 'bg-danger';
    }

    // Pin indicator shown next to status
    const pinIndicator = file.is_pinned
        ? '<i class="bi bi-pin-fill text-secondary me-1" title="File is pinned"></i>'
        : '';

    row.innerHTML = `
        <td class="file-path-cell"><code>${escapeHtml(file.file_path)}</code></td>
        <td>${storageBadge}</td>
        <td class="d-none d-lg-table-cell">${storageLocationHtml}</td>
        <td class="d-none d-md-table-cell">${formatBytes(file.file_size)}</td>
        <td class="d-none d-xl-table-cell"><small>${mtime}</small></td>
        <td class="d-none d-xl-table-cell"><small>${atime}</small></td>
        <td class="d-none d-xl-table-cell"><small>${ctime}</small></td>
        <td>${pinIndicator}<span class="badge ${statusBadgeClass}">${escapeHtml(file.status)}</span></td>
        <td class="d-none d-lg-table-cell"><small>${formatDate(file.last_seen)}</small></td>
        <td class="d-none d-md-table-cell">${tagsHtml}</td>
        <td>${actionButton}</td>
    `;

    return row;
}

/**
 * Initialize virtual table
 */
function initVirtualTable() {
    const container = document.getElementById('virtualTableContainer');
    const tbody = document.querySelector('#filesTable tbody');

    if (!container || !tbody) {
        console.error('Virtual table elements not found');
        return;
    }

    virtualTable = new VirtualTable({
        container: container,
        tbody: tbody,
        rowHeight: 48,
        bufferSize: 10,
        renderRow: createFileRow,
        onNearEnd: loadMoreFiles,
        nearEndThreshold: 500
    });

    // Setup event delegation for action buttons
    tbody.addEventListener('click', handleRowAction);
}

/**
 * Handle action button clicks via event delegation
 */
function handleRowAction(event) {
    const button = event.target.closest('[data-action]');
    if (!button) return;

    const row = button.closest('tr');
    if (!row) return;

    const fileId = parseInt(row.dataset.fileId);
    const filePath = row.dataset.filePath;
    const action = button.dataset.action;

    switch (action) {
        case 'thaw':
            showThawModal(fileId, filePath);
            break;
        case 'freeze':
            showFreezeModal(fileId, filePath);
            break;
        case 'relocate':
            showRelocateModal(fileId, filePath);
            break;
        case 'manageTags':
            showManageTagsModal(fileId, filePath);
            break;
    }
}

// Handle metadata message from stream
function handleStreamMetadata(metadata) {
    totalItems = metadata.total;
    hasMoreData = metadata.has_more;
    nextCursor = metadata.next_cursor;
    pageSize = metadata.page_size || 200;

    const loadingTextEl = document.getElementById('loading-text');
    const progressContainer = document.getElementById('stream-progress-container');

    if (loadingTextEl) {
        loadingTextEl.textContent = `Loading ${totalItems.toLocaleString()} files...`;
    }
    if (progressContainer) {
        progressContainer.style.display = 'block';
    }

    // Show content container early so rows can be appended
    const contentEl = document.getElementById('files-content');
    if (contentEl && totalItems > 0) {
        contentEl.style.display = 'block';
    }
}

// Update progress bar during streaming
function updateStreamProgress(received, total) {
    const progressBar = document.getElementById('stream-progress');
    if (progressBar && total > 0) {
        const percent = Math.round((received / total) * 100);
        progressBar.style.width = `${percent}%`;
        progressBar.setAttribute('aria-valuenow', percent);
    }
}

// Handle stream completion
function handleStreamComplete(message) {
    console.log(`Stream complete: ${message.count} files in ${message.duration_ms}ms`);
    hasMoreData = message.has_more;
    nextCursor = message.next_cursor;
    renderStreamSummary();
}

// Handle stream error
function handleStreamError(message) {
    console.error('Stream error:', message.message);
    showNotification(`Error loading files: ${message.message}. Received ${message.partial_count} files before error.`, 'error');
}

// Render summary after streaming completes
function renderStreamSummary() {
    const paginationEl = document.getElementById('pagination-controls');
    if (!paginationEl) return;

    const loadedCount = virtualTable ? virtualTable.getCount() : 0;
    const moreText = hasMoreData ? ' (scroll for more)' : '';

    paginationEl.innerHTML = `
        <p class="text-center text-muted">
            Showing ${loadedCount.toLocaleString()} of ${totalItems.toLocaleString()} files${moreText}
        </p>
    `;
}

// Process NDJSON stream
async function processNDJSONStream(body) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let receivedCount = 0;

    try {
        while (true) {
            const { done, value } = await reader.read();

            if (done) break;

            // Decode chunk and add to buffer
            buffer += decoder.decode(value, { stream: true });

            // Process complete lines
            const lines = buffer.split('\n');
            buffer = lines.pop(); // Keep incomplete line in buffer

            for (const line of lines) {
                if (!line.trim()) continue;

                try {
                    const message = JSON.parse(line);

                    switch (message.type) {
                        case 'metadata':
                            handleStreamMetadata(message);
                            break;
                        case 'file':
                            if (virtualTable) {
                                virtualTable.appendData(message.data);
                            }
                            receivedCount++;
                            updateStreamProgress(receivedCount, totalItems);
                            break;
                        case 'complete':
                            handleStreamComplete(message);
                            break;
                        case 'error':
                            handleStreamError(message);
                            break;
                    }
                } catch (parseError) {
                    console.error('Failed to parse NDJSON line:', line, parseError);
                }
            }
        }

        // Process any remaining buffer content
        if (buffer.trim()) {
            try {
                const message = JSON.parse(buffer);
                if (message.type === 'complete') {
                    handleStreamComplete(message);
                } else if (message.type === 'error') {
                    handleStreamError(message);
                }
            } catch (e) {
                console.error('Failed to parse final buffer:', buffer);
            }
        }
    } finally {
        reader.releaseLock();
    }
}

/**
 * Load more files (infinite scroll)
 */
async function loadMoreFiles() {
    if (isLoadingMore || !hasMoreData || !nextCursor) {
        return;
    }

    isLoadingMore = true;

    // Show loading indicator
    const paginationEl = document.getElementById('pagination-controls');
    if (paginationEl) {
        paginationEl.innerHTML = `
            <p class="text-center text-muted">
                <span class="spinner-border spinner-border-sm" role="status"></span>
                Loading more files...
            </p>
        `;
    }

    try {
        const params = buildQueryParams();
        params.append('cursor', nextCursor);

        const url = `${API_BASE_URL}/files?${params.toString()}`;
        const response = await fetch(url);

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        await processNDJSONStream(response.body);
        renderStreamSummary();

    } catch (error) {
        console.error('Error loading more files:', error);
        showNotification(`Failed to load more files: ${error.message}`, 'error');
    } finally {
        isLoadingMore = false;
    }
}

/**
 * Build query parameters for API request
 */
function buildQueryParams() {
    const params = new URLSearchParams({
        sort_by: currentSortBy,
        sort_order: currentSortOrder,
        page_size: pageSize.toString()
    });

    if (currentPathId) {
        params.append('path_id', currentPathId);
    }
    if (currentStorageType) {
        params.append('storage_type', currentStorageType);
    }
    if (currentFileStatus) {
        params.append('status', currentFileStatus);
    }
    if (currentSearch) {
        params.append('search', currentSearch);
    }
    if (currentTagIds.length > 0) {
        params.append('tag_ids', currentTagIds.join(','));
    }

    return params;
}

// Load and render files list via streaming
async function loadFilesList() {
    const loadingEl = document.getElementById('files-loading');
    const contentEl = document.getElementById('files-content');
    const emptyEl = document.getElementById('no-files-message');
    const paginationEl = document.getElementById('pagination-controls');

    // Abort any in-progress stream
    if (currentAbortController) {
        currentAbortController.abort();
    }
    currentAbortController = new AbortController();

    // Reset pagination state
    nextCursor = null;
    hasMoreData = false;
    isLoadingMore = false;

    // Show loading state
    if (loadingEl) loadingEl.style.display = 'block';
    if (contentEl) contentEl.style.display = 'none';
    if (emptyEl) emptyEl.style.display = 'none';
    if (paginationEl) paginationEl.innerHTML = '';

    // Reset virtual table
    if (virtualTable) {
        virtualTable.reset();
    }

    // Reset progress
    const loadingTextEl = document.getElementById('loading-text');
    const progressContainer = document.getElementById('stream-progress-container');
    const progressBar = document.getElementById('stream-progress');
    if (loadingTextEl) loadingTextEl.textContent = 'Loading files...';
    if (progressContainer) progressContainer.style.display = 'none';
    if (progressBar) progressBar.style.width = '0%';

    // Reset state
    totalItems = 0;

    try {
        const params = buildQueryParams();
        const url = `${API_BASE_URL}/files?${params.toString()}`;

        const response = await fetch(url, {
            signal: currentAbortController.signal
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        // Process NDJSON stream
        await processNDJSONStream(response.body);

        // Show content or empty message
        if (totalItems === 0) {
            if (emptyEl) emptyEl.style.display = 'block';
        } else {
            if (contentEl) contentEl.style.display = 'block';
        }

        updateSortIndicators();

    } catch (error) {
        if (error.name === 'AbortError') {
            console.log('Stream aborted');
            return;
        }
        console.error('Error loading files:', error);
        showNotification(`Failed to load files: ${error.message}`, 'error');
        if (emptyEl) emptyEl.style.display = 'block';
    } finally {
        if (loadingEl) loadingEl.style.display = 'none';
        currentAbortController = null;
    }
}

// Search function
function performSearch() {
    const searchInput = document.getElementById('search_input');
    currentSearch = searchInput ? searchInput.value.trim() : '';
    loadFilesList();
}

// Clear search
function clearSearch() {
    const searchInput = document.getElementById('search_input');
    if (searchInput) searchInput.value = '';
    currentSearch = '';
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

        // Setup status filter
        const statusSelect = document.getElementById('status_filter');
        if (statusSelect) {
            statusSelect.addEventListener('change', function() {
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
    const statusSelect = document.getElementById('status_filter');
    const tagSelect = document.getElementById('tag_filter');

    currentPathId = pathSelect && pathSelect.value ? parseInt(pathSelect.value) : null;
    currentStorageType = storageSelect && storageSelect.value ? storageSelect.value : null;
    currentFileStatus = statusSelect && statusSelect.value ? statusSelect.value : null;

    // Get selected tag IDs from multi-select
    if (tagSelect) {
        const selectedOptions = Array.from(tagSelect.selectedOptions);
        currentTagIds = selectedOptions
            .map(opt => opt.value)
            .filter(val => val !== '') // Filter out "All Tags" option
            .map(val => parseInt(val));
    }

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
    const pinCheckbox = document.getElementById('thawPinCheckbox');
    const pin = pinCheckbox ? pinCheckbox.checked : false;

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

// Toggle pin status for a file
async function togglePin(inventoryId, currentlyPinned) {
    const method = currentlyPinned ? 'DELETE' : 'POST';
    const action = currentlyPinned ? 'unpin' : 'pin';

    try {
        const response = await fetch(`${API_BASE_URL}/files/${inventoryId}/pin`, {
            method: method
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || `Failed to ${action} file`);
        }

        const data = await response.json();
        showNotification(data.message);

        // Reload files to show updated pin status
        loadFilesList();
    } catch (error) {
        console.error(`Error ${action}ning file:`, error);
        showNotification(`Error ${action}ning file: ${error.message}`, 'error');
    }
}

// Freeze Modal Management
let freezeModal = null;
let currentFreezeInventoryId = null;

// Show freeze modal
async function showFreezeModal(inventoryId, filePath) {
    currentFreezeInventoryId = inventoryId;

    const modal = document.getElementById('freezeModal');
    if (!modal) return;

    // Reset modal state
    document.getElementById('freezeFileName').textContent = filePath;
    document.getElementById('freezeLocationSelect').innerHTML = '<option value="">Loading locations...</option>';
    document.getElementById('freezeLocationSelect').disabled = true;
    document.getElementById('confirmFreezeBtn').disabled = true;
    document.getElementById('freezePinCheckbox').checked = false;

    const errorEl = document.getElementById('freezeError');
    if (errorEl) {
        errorEl.textContent = '';
        errorEl.classList.add('d-none');
    }

    // Show modal
    if (!freezeModal) {
        freezeModal = new bootstrap.Modal(modal);
    }
    freezeModal.show();

    // Load freeze options
    await loadFreezeOptions(inventoryId);
}

// Load freeze options for a file
async function loadFreezeOptions(inventoryId) {
    const selectEl = document.getElementById('freezeLocationSelect');
    const errorEl = document.getElementById('freezeError');
    const confirmBtn = document.getElementById('confirmFreezeBtn');

    try {
        const response = await fetch(`${API_BASE_URL}/files/freeze/${inventoryId}/options`);
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to load options');
        }

        const data = await response.json();

        if (!data.can_freeze || data.available_locations.length === 0) {
            // No locations available
            selectEl.innerHTML = '<option value="">No storage locations available</option>';
            selectEl.disabled = true;
            confirmBtn.disabled = true;
            if (errorEl) {
                errorEl.textContent = 'No cold storage locations are configured for this path.';
                errorEl.classList.remove('d-none');
            }
        } else {
            // Filter to available locations only
            const availableLocations = data.available_locations.filter(loc => loc.available);

            if (availableLocations.length === 0) {
                selectEl.innerHTML = '<option value="">No storage locations currently accessible</option>';
                selectEl.disabled = true;
                confirmBtn.disabled = true;
                if (errorEl) {
                    errorEl.textContent = 'All configured storage locations are offline or inaccessible.';
                    errorEl.classList.remove('d-none');
                }
            } else if (availableLocations.length === 1) {
                // Only one location - preselect it
                selectEl.innerHTML = availableLocations.map(loc =>
                    `<option value="${loc.id}" selected>${escapeHtml(loc.name)} (${escapeHtml(loc.path)})</option>`
                ).join('');
                selectEl.disabled = false;
                confirmBtn.disabled = false;
            } else {
                // Multiple locations - let user choose
                selectEl.innerHTML = '<option value="">Select storage location...</option>' +
                    availableLocations.map(loc =>
                        `<option value="${loc.id}">${escapeHtml(loc.name)} (${escapeHtml(loc.path)})</option>`
                    ).join('');
                selectEl.disabled = false;
            }
        }
    } catch (error) {
        console.error('Error loading freeze options:', error);
        if (errorEl) {
            errorEl.textContent = error.message;
            errorEl.classList.remove('d-none');
        }
        selectEl.innerHTML = '<option value="">Error loading locations</option>';
        selectEl.disabled = true;
        confirmBtn.disabled = true;
    }
}

// Handle freeze location selection
function onFreezeLocationChange() {
    const selectEl = document.getElementById('freezeLocationSelect');
    const confirmBtn = document.getElementById('confirmFreezeBtn');

    if (confirmBtn) {
        confirmBtn.disabled = !selectEl || !selectEl.value;
    }
}

// Freeze file action
async function freezeFile() {
    const selectEl = document.getElementById('freezeLocationSelect');
    const storageLocationId = selectEl ? parseInt(selectEl.value) : null;
    const pinCheckbox = document.getElementById('freezePinCheckbox');
    const pin = pinCheckbox ? pinCheckbox.checked : false;
    const confirmBtn = document.getElementById('confirmFreezeBtn');
    const errorEl = document.getElementById('freezeError');

    if (!storageLocationId || !currentFreezeInventoryId) return;

    // Disable button during operation
    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status"></span> Freezing...';
    }

    // Clear previous error
    if (errorEl) {
        errorEl.textContent = '';
        errorEl.classList.add('d-none');
    }

    try {
        const response = await fetch(`${API_BASE_URL}/files/freeze/${currentFreezeInventoryId}?storage_location_id=${storageLocationId}&pin=${pin}`, {
            method: 'POST'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to freeze file');
        }

        const data = await response.json();
        showNotification(`File sent to ${data.storage_location.name}` + (pin ? ' and pinned' : ''));

        // Close modal
        if (freezeModal) freezeModal.hide();

        // Reload files to show updated state
        loadFilesList();
    } catch (error) {
        console.error('Error freezing file:', error);
        showNotification(`Error freezing file: ${error.message}`, 'error');
        if (errorEl) {
            errorEl.textContent = error.message;
            errorEl.classList.remove('d-none');
        }
    } finally {
        // Reset button
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.innerHTML = '<i class="bi bi-snow"></i> Send to Fridge';
        }
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
    currentFileStatus = urlParams.get('status') || null;

    // Set initial value for status filter
    const statusSelect = document.getElementById('status_filter');
    if (statusSelect && currentFileStatus) {
        statusSelect.value = currentFileStatus;
    }

    // Initialize virtual table
    initVirtualTable();

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

    // Setup freeze modal
    const freezeLocationSelect = document.getElementById('freezeLocationSelect');
    if (freezeLocationSelect) {
        freezeLocationSelect.addEventListener('change', onFreezeLocationChange);
    }

    const confirmFreezeBtn = document.getElementById('confirmFreezeBtn');
    if (confirmFreezeBtn) {
        confirmFreezeBtn.addEventListener('click', freezeFile);
    }

    // Reset thaw modal checkbox when modal is shown
    const thawModal = document.getElementById('thawModal');
    if (thawModal) {
        thawModal.addEventListener('show.bs.modal', function() {
            const checkbox = document.getElementById('thawPinCheckbox');
            if (checkbox) checkbox.checked = false;
        });
    }
});

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (currentAbortController) {
        currentAbortController.abort();
    }
    if (virtualTable) {
        virtualTable.destroy();
    }
});
