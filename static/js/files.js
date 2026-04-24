// Files browser JavaScript - AG Grid implementation with streaming data
const API_BASE_URL = '/api/v1';

// Filter state
let currentSearch = '';
let currentPathId = null;
let currentStorageType = null;
let currentFileStatus = null;
let currentTagIds = [];
let currentSortBy = 'last_seen';
let currentSortOrder = 'desc';
let currentExtension = '';
let currentMimeType = '';
let currentHasChecksum = null;
let currentIsPinned = null;
let currentMinSize = null;
let currentMaxSize = null;
let currentMinMtime = null;
let currentMaxMtime = null;
let currentStorageLocationId = null;
let totalItems = 0;

// Pagination state
let nextCursor = null;
let hasMoreData = false;
let isLoadingMore = false;
let pageSize = 200;

// Streaming state
let currentAbortController = null;
// Monotonically increasing counter — each loadFilesList() call gets a new version.
// processNDJSONStream checks this to discard messages from superseded requests.
let currentStreamVersion = 0;

// Maximum rows to keep in memory. When exceeded the oldest rows are evicted
// from both allRowData and the grid to maintain a sliding window.
const MAX_ROW_CACHE = 10000;

// Stream timeout — abort if no new data arrives within this many ms.
const STREAM_TIMEOUT_MS = 30000;

// AG Grid instance
let gridApi = null;
let allRowData = [];
let activeGridDropdown = null;

// Debounce helper — returns a version of fn that waits delayMs after the last call.
function debounce(fn, delayMs) {
    let timer = null;
    return function (...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delayMs);
    };
}

// Utility functions
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function parseStorageFilterValue(value) {
    if (!value) {
        return { storageType: null, storageLocationId: null };
    }

    if (value.startsWith('location:')) {
        const storageLocationId = parseInt(value.slice('location:'.length), 10);
        return {
            storageType: 'cold',
            storageLocationId: Number.isNaN(storageLocationId) ? null : storageLocationId
        };
    }

    return { storageType: value, storageLocationId: null };
}

function storageFilterValueFromState() {
    if (currentStorageLocationId !== null && currentStorageLocationId !== undefined) {
        return `location:${currentStorageLocationId}`;
    }
    return currentStorageType || '';
}

function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    if (bytes < 1024) return bytes + ' Bytes';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(2) + ' MB';
    return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB';
}

function formatDate(dateString) {
    if (!dateString) return '';
    const date = new Date(dateString);
    return date.toLocaleString();
}

// Notification function - uses toast notifications from app.js
function showNotification(message, type = 'success') {
    showToast(message, type);
}

// Cell Renderers for AG Grid
function filePathCellRenderer(params) {
    if (!params.value) return '';
    const file = params.data || {};
    const displayPath = escapeHtml(params.value);
    const storageReference = file.storage_reference ? escapeHtml(file.storage_reference) : null;

    if (storageReference) {
        return `
            <div class="file-path-cell">
                <code>${displayPath}</code>
                <div><small class="text-muted" title="${storageReference}">Stored as ${storageReference}</small></div>
            </div>
        `;
    }

    return `<code class="file-path-cell">${displayPath}</code>`;
}

function destinationPathCellRenderer(params) {
    const file = params.data;
    if (!file || !file.destination_path) {
        return '<span class="text-muted">-</span>';
    }

    const safePath = escapeHtml(file.destination_path);
    return `
        <div class="text-truncate" style="max-width: 240px;" title="${safePath}">
            <code class="file-path-cell">${safePath}</code>
        </div>
    `;
}

function storageCellRenderer(params) {
    const storageType = params.value;
    if (storageType === 'hot') {
        return '<span class="badge bg-success">Hot</span>';
    } else if (storageType === 'cold') {
        return '<span class="badge bg-info">Cold</span>';
    }
    return '';
}

function storageLocationCellRenderer(params) {
    const file = params.data;
    if (!file) {
        return '<span class="text-muted">-</span>';
    }
    if (file.storage_type !== 'cold' || !file.storage_location) {
        if (file.storage_type === 'hot' && file.path_name) {
            return `<span class="badge bg-light text-dark border" title="Currently in monitored hot path">${escapeHtml(file.path_name)}</span>`;
        }
        return '<span class="text-muted">-</span>';
    }
    const location = file.storage_location;
    const backendType = (location.backend_type || 'unknown').toUpperCase();
    const locationName = escapeHtml(location.name || 'Unknown');
    const backendPath = escapeHtml(location.path || '');
    const isUnavailable = location.available === false;

    const driveHint = location.backend_type === 'local'
        ? (location.local_drive_label || location.local_drive_identifier || null)
        : backendPath;

    const driveHintHtml = driveHint
        ? `<div><small class="text-muted">${escapeHtml(driveHint)}</small></div>`
        : '';

    if (isUnavailable) {
        return `<div title="Storage unavailable - reconnect drive to perform operations">
            <span class="badge bg-danger"><i class="bi bi-exclamation-triangle me-1"></i>${locationName}</span>
            <span class="badge bg-dark ms-1">${escapeHtml(backendType)}</span>
            ${driveHintHtml}
        </div>`;
    }

    return `<div title="${backendPath}">
        <span class="badge bg-secondary">${locationName}</span>
        <span class="badge bg-light text-dark border ms-1">${escapeHtml(backendType)}</span>
        ${driveHintHtml}
    </div>`;
}

function sizeCellRenderer(params) {
    if (params.value === null || params.value === undefined) return '';
    return formatBytes(params.value);
}

function dateCellRenderer(params) {
    if (!params.value) return '<span class="text-muted">N/A</span>';
    return `<small>${formatDate(params.value)}</small>`;
}

function statusCellRenderer(params) {
    const file = params.data;
    if (!file) return '';
    let statusBadgeClass = 'bg-secondary';
    if (file.status === 'active') {
        statusBadgeClass = 'bg-success';
    } else if (file.status === 'migrating') {
        statusBadgeClass = 'bg-warning text-dark';
    } else if (file.status === 'missing' || file.status === 'deleted') {
        statusBadgeClass = 'bg-danger';
    }

    const pinIndicator = file.is_pinned
        ? '<i class="bi bi-pin-fill text-secondary me-1" title="File is pinned"></i>'
        : '';

    return `${pinIndicator}<span class="badge ${statusBadgeClass}">${escapeHtml(file.status)}</span>`;
}

function tagsCellRenderer(params) {
    const file = params.data;
    if (!file) return '';
    let tagsHtml = '';
    if (file.tags && file.tags.length > 0) {
        tagsHtml = file.tags.map(ft => {
            const rawColor = ft.tag && ft.tag.color ? ft.tag.color : '#6c757d';
            // Validate hex color to prevent style injection
            const color = /^#[0-9a-fA-F]{3,6}$/.test(rawColor) ? rawColor : '#6c757d';
            const name = ft.tag && ft.tag.name ? ft.tag.name : 'Unknown';
            return `<span class="badge me-1" style="background-color: ${color};">${escapeHtml(name)}</span>`;
        }).join('');
    }
    tagsHtml += `<button type="button" class="btn btn-sm btn-outline-primary" data-action="manageTags" data-file-id="${file.id}" data-file-path="${escapeHtml(file.file_path)}" title="Manage tags">
        <i class="bi bi-tags"></i>
    </button>`;
    return tagsHtml;
}

function actionsCellRenderer(params) {
    const file = params.data;
    if (!file) return '';
    const isMigrating = file.status === 'migrating';
    const storageUnavailable = file.storage_type === 'cold' &&
        file.storage_location && file.storage_location.available === false;

    if (isMigrating) {
        return `<span class="text-warning">
            <span class="spinner-border spinner-border-sm" role="status"></span>
            <span class="d-none d-sm-inline">Migrating...</span>
        </span>`;
    }

    if (storageUnavailable) {
        return `
            <div class="btn-group btn-group-sm" role="group">
                <button type="button" class="btn btn-outline-secondary" data-action="details" data-file-id="${file.id}" title="View file details">
                    <i class="bi bi-info-circle"></i><span class="d-none d-lg-inline"> Details</span>
                </button>
                <button type="button" class="btn btn-outline-danger" disabled title="Storage unavailable - reconnect drive to thaw or relocate">
                    <i class="bi bi-hdd-network"></i><span class="d-none d-lg-inline"> Offline</span>
                </button>
            </div>
        `;
    }

    const isCold = file.storage_type === 'cold';
    const primaryAction = isCold ? 'thaw' : 'freeze';
    const primaryIcon = isCold ? 'bi-fire' : 'bi-snow';
    const primaryText = isCold ? 'Thaw' : 'Fridge';
    const primaryClass = isCold ? 'btn-warning' : 'btn-info';

    return `
        <div class="btn-group btn-group-sm" role="group">
            <button type="button" class="btn ${primaryClass}" data-action="${primaryAction}" data-file-id="${file.id}" data-file-path="${escapeHtml(file.file_path)}" title="${isCold ? 'Move file back to hot storage' : 'Send to cold storage'}">
                <i class="bi ${primaryIcon}"></i><span class="d-none d-lg-inline"> ${primaryText}</span>
            </button>
            <div class="btn-group btn-group-sm" role="group">
                <button type="button" class="btn btn-outline-secondary dropdown-toggle dropdown-toggle-split" data-bs-toggle="dropdown" data-bs-boundary="window" data-bs-auto-close="true" aria-expanded="false">
                    <span class="visually-hidden">Toggle Dropdown</span>
                </button>
                <ul class="dropdown-menu dropdown-menu-end shadow-sm">
                    <li><button class="dropdown-item" type="button" data-action="details" data-file-id="${file.id}"><i class="bi bi-info-circle me-2"></i>Details</button></li>
                    ${isCold ? `<li><button class="dropdown-item" type="button" data-action="relocate" data-file-id="${file.id}" data-file-path="${escapeHtml(file.file_path)}"><i class="bi bi-arrow-right-circle me-2"></i>Relocate</button></li>` : ''}
                    <li><button class="dropdown-item" type="button" data-action="migrate" data-file-id="${file.id}" data-file-path="${escapeHtml(file.file_path)}"><i class="bi bi-hdd-network me-2"></i>Migrate</button></li>
                    <li><hr class="dropdown-divider"></li>
                    <li><button class="dropdown-item" type="button" data-action="manageTags" data-file-id="${file.id}" data-file-path="${escapeHtml(file.file_path)}"><i class="bi bi-tags me-2"></i>Manage Tags</button></li>
                </ul>
            </div>
        </div>
    `;
}

// AG Grid Column Definitions
const columnDefs = [
    {
        headerName: '',
        field: 'selected',
        width: 50,
        maxWidth: 50,
        checkboxSelection: true,
        headerCheckboxSelection: true,
        headerCheckboxSelectionFilteredOnly: true,
        sortable: false,
        resizable: false,
        pinned: 'left'
    },
    {
        field: 'display_file_path',
        headerName: 'File Path',
        cellRenderer: filePathCellRenderer,
        flex: 2,
        minWidth: 200,
        sortable: true,
        resizable: true,
        tooltipField: 'display_file_path'
    },
    {
        field: 'storage_type',
        headerName: 'Storage',
        cellRenderer: storageCellRenderer,
        width: 90,
        sortable: true,
        resizable: true
    },
    {
        field: 'storage_location_name',
        headerName: 'Location',
        cellRenderer: storageLocationCellRenderer,
        width: 250,
        sortable: true,
        resizable: true
    },
    {
        field: 'destination_path',
        headerName: 'Destination',
        cellRenderer: destinationPathCellRenderer,
        width: 260,
        sortable: false,
        resizable: true,
        tooltipField: 'destination_path'
    },
    {
        field: 'file_size',
        headerName: 'Size',
        cellRenderer: sizeCellRenderer,
        width: 100,
        sortable: true,
        resizable: true,
        type: 'numericColumn'
    },
    {
        field: 'file_mtime',
        headerName: 'Modified',
        cellRenderer: dateCellRenderer,
        width: 160,
        sortable: true,
        resizable: true
    },
    {
        field: 'file_atime',
        headerName: 'Accessed',
        cellRenderer: dateCellRenderer,
        width: 160,
        sortable: true,
        resizable: true
    },
    {
        field: 'file_ctime',
        headerName: 'Changed',
        cellRenderer: dateCellRenderer,
        width: 160,
        sortable: true,
        resizable: true
    },
    {
        field: 'status',
        headerName: 'Status',
        cellRenderer: statusCellRenderer,
        width: 110,
        sortable: true,
        resizable: true
    },
    {
        field: 'last_seen',
        headerName: 'Last Seen',
        cellRenderer: dateCellRenderer,
        width: 160,
        sortable: true,
        resizable: true
    },
    {
        headerName: 'Tags',
        cellRenderer: tagsCellRenderer,
        width: 150,
        sortable: false,
        resizable: true,
        autoHeight: true
    },
    {
        headerName: 'Actions',
        cellRenderer: actionsCellRenderer,
        width: 150,
        sortable: false,
        resizable: false,
        pinned: 'right'
    }
];

// AG Grid Options
const gridOptions = {
    theme: 'legacy',
    columnDefs: columnDefs,
    rowData: [],
    defaultColDef: {
        resizable: true,
        sortable: true
    },
    // Animations add CPU cost with large datasets; disable for enterprise scale.
    animateRows: false,
    // Pre-render 25 rows beyond the visible viewport for smooth scrolling.
    rowBuffer: 25,
    rowHeight: 48,
    headerHeight: 40,
    suppressCellFocus: true,
    enableCellTextSelection: true,
    ensureDomOrder: true,
    getRowId: params => String(params.data.id),
    onSortChanged: onGridSortChanged,
    onGridReady: onGridReady,
    onBodyScroll: onBodyScroll,
    onSelectionChanged: onSelectionChanged,
    tooltipShowDelay: 500,
    rowSelection: 'multiple',
    suppressRowClickSelection: true,
    suppressRowTransform: true,
    overlayLoadingTemplate: '<span class="spinner-border spinner-border-sm text-primary" role="status"></span> Loading...',
    overlayNoRowsTemplate: '<span class="text-muted">No files found</span>'
};

// Handle AG Grid sort changes
function onGridSortChanged(event) {
    const sortModel = event.api.getColumnState().filter(col => col.sort);
    if (sortModel.length > 0) {
        const sortedColumn = sortModel[0];
        const fieldToSortBy = {
            'display_file_path': 'file_path',
            'storage_type': 'storage_type',
            'file_size': 'file_size',
            'file_mtime': 'file_mtime',
            'file_atime': 'file_atime',
            'file_ctime': 'file_ctime',
            'status': 'status',
            'last_seen': 'last_seen',
            'storage_location_name': 'storage_location'
        };

        const apiSortField = fieldToSortBy[sortedColumn.colId];
        if (apiSortField) {
            currentSortBy = apiSortField;
            currentSortOrder = sortedColumn.sort;
            syncStateToUrl();
            loadFilesList();
        }
    }
}

// Handle grid ready
function onGridReady(params) {
    gridApi = params.api;

    // Add click handler for action buttons
    const filesGridEl = document.getElementById('filesGrid');
    filesGridEl.addEventListener('click', handleActionClick);
    document.addEventListener('click', handlePortaledDropdownAction);
    filesGridEl.addEventListener('show.bs.dropdown', function (event) {
        bootstrap.Dropdown.getOrCreateInstance(event.target, gridDropdownConfig());
        moveDropdownMenuToBody(event.target);
    });
    filesGridEl.addEventListener('hidden.bs.dropdown', restoreDropdownMenu);

    // Fix #7: Ctrl/Cmd+A selects all loaded rows when the grid is focused.
    filesGridEl.addEventListener('keydown', function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key === 'a') {
            e.preventDefault();
            if (gridApi) gridApi.selectAll();
        }
    });

    // Initialize dropdowns with proper Popper.js config for overflow issues
    initializeDropdowns();

    // Re-initialize dropdowns when grid updates
    gridApi.addEventListener('rowDataUpdated', initializeDropdowns);
    gridApi.addEventListener('modelUpdated', function () {
        hideOpenGridDropdown();
        initializeDropdowns();
    });
}

// Initialize Bootstrap dropdowns with custom Popper.js config
function initializeDropdowns() {
    // Use setTimeout to ensure DOM is ready
    setTimeout(() => {
        const dropdownToggles = document.querySelectorAll('#filesGrid [data-bs-toggle="dropdown"]');
        dropdownToggles.forEach(toggle => {
            bootstrap.Dropdown.getOrCreateInstance(toggle, gridDropdownConfig());
        });
    }, 100);
}

function gridDropdownConfig() {
    return {
        boundary: 'window',
        popperConfig: function (defaultConfig) {
            return {
                ...defaultConfig,
                strategy: 'fixed',
                modifiers: [
                    ...defaultConfig.modifiers,
                    {
                        name: 'preventOverflow',
                        options: {
                            boundary: 'viewport'
                        }
                    }
                ]
            };
        }
    };
}

function getDropdownMenuForToggle(toggle) {
    return toggle.closest('.btn-group')?.querySelector('.dropdown-menu') || null;
}

function moveDropdownMenuToBody(toggle) {
    const menu = getDropdownMenuForToggle(toggle);
    if (!menu || menu.parentElement === document.body) {
        return;
    }

    activeGridDropdown = {
        toggle,
        menu,
        parent: menu.parentNode,
        nextSibling: menu.nextSibling
    };
    menu.classList.add('files-grid-dropdown-menu');
    document.body.appendChild(menu);
}

function restoreDropdownMenu() {
    if (!activeGridDropdown) {
        return;
    }

    const { menu, parent, nextSibling } = activeGridDropdown;
    menu.classList.remove('files-grid-dropdown-menu');
    if (parent && parent.isConnected) {
        parent.insertBefore(menu, nextSibling && nextSibling.isConnected ? nextSibling : null);
    } else if (menu.isConnected) {
        menu.remove();
    }
    activeGridDropdown = null;
}

function hideOpenGridDropdown() {
    const openToggle = document.querySelector('#filesGrid [data-bs-toggle="dropdown"].show');
    if (openToggle) {
        const instance = bootstrap.Dropdown.getInstance(openToggle);
        if (instance) {
            instance.hide();
        }
    } else {
        restoreDropdownMenu();
    }
}

// Handle body scroll for infinite scrolling
function onBodyScroll(event) {
    hideOpenGridDropdown();

    if (!gridApi || isLoadingMore || !hasMoreData) return;

    const verticalScrollPosition = event.top;
    const gridBody = document.querySelector('.ag-body-viewport');
    if (!gridBody) return;

    const maxScrollTop = gridBody.scrollHeight - gridBody.clientHeight;

    // Load more when near the bottom
    if (maxScrollTop - verticalScrollPosition < 500) {
        loadMoreFiles();
    }
}

// Handle action button clicks
function handleActionClick(event) {
    const button = event.target.closest('[data-action]');
    if (!button) return;

    const action = button.dataset.action;
    const fileId = parseInt(button.dataset.fileId);
    const filePath = button.dataset.filePath;

    switch (action) {
        case 'details':
            window.location.href = `/files/${fileId}`;
            break;
        case 'thaw':
            showThawModal(fileId, filePath);
            break;
        case 'freeze':
            showFreezeModal(fileId, filePath);
            break;
        case 'relocate':
            showRelocateModal(fileId, filePath);
            break;
        case 'migrate':
            showRemoteMigrationModal(fileId, filePath);
            break;
        case 'manageTags':
            showManageTagsModal(fileId, filePath);
            break;
    }
}

function handlePortaledDropdownAction(event) {
    if (!event.target.closest('.files-grid-dropdown-menu')) {
        return;
    }
    handleActionClick(event);
}

// Initialize AG Grid
function initGrid() {
    const gridDiv = document.getElementById('filesGrid');
    if (!gridDiv) {
        console.error('Grid container not found');
        return;
    }

    window.agGrid.createGrid(gridDiv, gridOptions);
}

async function waitForAgGrid(maxWaitMs = 5000) {
    if (window.agGrid) {
        return;
    }

    const startTime = Date.now();
    while (!window.agGrid) {
        if (Date.now() - startTime >= maxWaitMs) {
            throw new Error('AG Grid library did not finish loading in time');
        }

        await new Promise(resolve => window.setTimeout(resolve, 50));
    }
}

// Handle metadata message from stream.
// append=true means this is a load-more page — don't touch the loading overlay.
function handleStreamMetadata(metadata, append = false) {
    totalItems = metadata.total;
    hasMoreData = metadata.has_more;
    nextCursor = metadata.next_cursor;
    pageSize = metadata.page_size || 200;

    if (!append) {
        const loadingTextEl = document.getElementById('loading-text');
        const progressContainer = document.getElementById('stream-progress-container');
        if (loadingTextEl) {
            loadingTextEl.textContent = `Loading ${totalItems.toLocaleString()} files...`;
        }
        if (progressContainer) {
            progressContainer.style.display = 'block';
        }
    }
}

// Update progress bar during streaming.
// Progress reflects how much of the CURRENT page has arrived (received/pageSize),
// not the fraction of total matching files — that would show <1% for large datasets
// and jump to 100% when the first small page completes.
function updateStreamProgress(received) {
    const progressBar = document.getElementById('stream-progress');
    if (progressBar && pageSize > 0) {
        const percent = Math.min(100, Math.round((received / pageSize) * 100));
        progressBar.style.width = `${percent}%`;
        progressBar.setAttribute('aria-valuenow', percent);
    }
}

// Handle stream completion
function handleStreamComplete(message) {
    console.log(`Stream complete: ${message.count} files in ${message.duration_ms}ms`);
    hasMoreData = message.has_more;
    nextCursor = message.next_cursor;
    updateStatusBar();
}

// Handle stream error
function handleStreamError(message) {
    console.error('Stream error:', message.message);
    showNotification(`Error loading files: ${message.message}. Received ${message.partial_count} files before error.`, 'error');
}

// Update status bar
function updateStatusBar() {
    const statusBar = document.getElementById('status-bar');
    if (!statusBar) return;

    const loadedCount = gridApi ? gridApi.getDisplayedRowCount() : allRowData.length;
    const moreText = hasMoreData ? ' (scroll for more)' : '';

    statusBar.textContent = `Showing ${loadedCount.toLocaleString()} of ${totalItems.toLocaleString()} files${moreText}`;
}

// Flush a batch of file rows into the grid using applyTransaction (fix #11).
// Uses a sliding window (fix #6): when allRowData would exceed MAX_ROW_CACHE,
// evict the oldest rows from both allRowData and the grid before adding new ones.
function flushBatchToGrid(batch) {
    if (!batch.length) return;

    if (allRowData.length + batch.length > MAX_ROW_CACHE) {
        const overflow = allRowData.length + batch.length - MAX_ROW_CACHE;
        const toEvict = allRowData.slice(0, overflow);
        allRowData = allRowData.slice(overflow).concat(batch);
        if (gridApi) gridApi.applyTransaction({ remove: toEvict, add: batch });
    } else {
        allRowData = allRowData.concat(batch);
        if (gridApi) gridApi.applyTransaction({ add: batch });
    }

    // Reveal the grid as soon as the first rows arrive
    const gridEl = document.getElementById('filesGrid');
    if (gridEl && gridEl.style.display === 'none' && allRowData.length > 0) {
        gridEl.style.display = 'block';
        const emptyEl = document.getElementById('no-files-message');
        if (emptyEl) emptyEl.style.display = 'none';
    }
}

// Process an NDJSON response stream.
//   append       — true when loading more pages (infinite scroll); false for fresh loads.
//   streamVersion — the currentStreamVersion value at the time the request was made;
//                   messages from superseded (stale) streams are silently discarded (fix #1).
async function processNDJSONStream(body, append = false, streamVersion = null) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let receivedCount = 0;
    const batchSize = 50;
    let batch = [];

    // Returns false if this stream has been superseded by a newer loadFilesList() call.
    function isCurrentStream() {
        return streamVersion === null || streamVersion === currentStreamVersion;
    }

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            // Fix #1: discard stale stream as soon as possible
            if (!isCurrentStream()) {
                reader.cancel();
                return;
            }

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.trim()) continue;
                if (!isCurrentStream()) { reader.cancel(); return; }

                try {
                    const message = JSON.parse(line);
                    switch (message.type) {
                        case 'metadata':
                            // Pass append so load-more doesn't overwrite the loading overlay (fix #2)
                            handleStreamMetadata(message, append);
                            break;
                        case 'file':
                            batch.push(message.data);
                            receivedCount++;
                            if (batch.length >= batchSize) {
                                flushBatchToGrid(batch);
                                batch = [];
                            }
                            // Fix #12: progress relative to current page, not total file count
                            if (!append) updateStreamProgress(receivedCount);
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

        // Flush any remaining partial batch
        if (batch.length > 0) {
            flushBatchToGrid(batch);
        }

        // Handle any leftover buffer content (e.g. final line without trailing newline)
        if (buffer.trim()) {
            try {
                const message = JSON.parse(buffer);
                if (message.type === 'complete') handleStreamComplete(message);
                else if (message.type === 'error') handleStreamError(message);
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

    try {
        const params = buildQueryParams();
        params.append('cursor', nextCursor);

        const url = `${API_BASE_URL}/files?${params.toString()}`;
        const response = await authenticatedFetch(url);

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        // Pass current stream version so a concurrent loadFilesList() cancels this stream (fix #1)
        await processNDJSONStream(response.body, true, currentStreamVersion);
        updateStatusBar();

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
    if (currentExtension) {
        params.append('extension', currentExtension);
    }
    if (currentMimeType) {
        params.append('mime_type', currentMimeType);
    }
    if (currentHasChecksum !== null) {
        params.append('has_checksum', currentHasChecksum);
    }
    if (currentIsPinned !== null) {
        params.append('is_pinned', currentIsPinned);
    }
    if (currentMinSize !== null) {
        params.append('min_size', currentMinSize);
    }
    if (currentMaxSize !== null) {
        params.append('max_size', currentMaxSize);
    }
    if (currentMinMtime !== null) {
        params.append('min_mtime', currentMinMtime);
    }
    if (currentMaxMtime !== null) {
        params.append('max_mtime', currentMaxMtime);
    }
    if (currentStorageLocationId !== null) {
        params.append('storage_location_id', currentStorageLocationId);
    }

    return params;
}

// Load and render files list via streaming
async function loadFilesList() {
    const loadingEl = document.getElementById('files-loading');
    const gridEl = document.getElementById('filesGrid');
    const emptyEl = document.getElementById('no-files-message');
    const statusBar = document.getElementById('status-bar');

    // Fix #4: Snapshot selected IDs before clearing the grid, so we can restore them
    // after the new data arrives (preserves selection across filter/sort changes).
    const selectionSnapshot = new Set(selectedFiles.map(f => f.id));

    // Fix #1: Increment version so any in-flight streams (including load-more) know to stop.
    const myVersion = ++currentStreamVersion;

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
    if (gridEl) gridEl.style.display = 'none';
    if (emptyEl) emptyEl.style.display = 'none';
    if (statusBar) statusBar.textContent = '';

    // Reset row data
    allRowData = [];
    if (gridApi) {
        gridApi.setGridOption('rowData', []);
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

    // Fix #2: Abort the stream if no data arrives within STREAM_TIMEOUT_MS.
    const timeoutId = setTimeout(() => {
        if (currentAbortController) currentAbortController.abort();
    }, STREAM_TIMEOUT_MS);

    try {
        const params = buildQueryParams();
        const url = `${API_BASE_URL}/files?${params.toString()}`;

        const response = await authenticatedFetch(url, {
            signal: currentAbortController.signal
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        // Process NDJSON stream — pass version so stale messages self-discard (fix #1)
        await processNDJSONStream(response.body, false, myVersion);

        // Show content or empty message based on actual received data
        if (allRowData.length === 0) {
            if (emptyEl) emptyEl.style.display = 'block';
            if (gridEl) gridEl.style.display = 'none';
        } else {
            if (gridEl) gridEl.style.display = 'block';
            if (emptyEl) emptyEl.style.display = 'none';
        }

        // Fix #4: Restore as many previously-selected rows as the new dataset contains.
        if (selectionSnapshot.size > 0 && gridApi) {
            let restored = 0;
            gridApi.forEachNode(node => {
                if (selectionSnapshot.has(node.data.id)) {
                    node.setSelected(true);
                    restored++;
                }
            });
            const lost = selectionSnapshot.size - restored;
            if (lost > 0) {
                showNotification(
                    `${lost} previously selected file${lost > 1 ? 's are' : ' is'} not visible with the current filters.`,
                    'warning'
                );
            }
        }

    } catch (error) {
        if (error.name === 'AbortError') {
            // Fix #2: Distinguish user-triggered aborts (filter change) from timeouts.
            if (myVersion !== currentStreamVersion) {
                // A newer request superseded this one — expected, not an error.
                return;
            }
            console.error('Stream timed out or was aborted');
            showNotification('Loading timed out. Check your connection and try again.', 'error');
            if (emptyEl) emptyEl.style.display = 'block';
            return;
        }
        console.error('Error loading files:', error);
        showNotification(`Failed to load files: ${error.message}`, 'error');
        if (emptyEl) emptyEl.style.display = 'block';
    } finally {
        clearTimeout(timeoutId);
        if (loadingEl) loadingEl.style.display = 'none';
        currentAbortController = null;
    }
}

// Reset all filters
function resetFilters() {
    // Reset search
    const searchInput = document.getElementById('search_input');
    if (searchInput) searchInput.value = '';
    currentSearch = '';

    // Reset selects
    const pathSelect = document.getElementById('path_id_filter');
    if (pathSelect) pathSelect.value = '';
    currentPathId = null;

    const storageSelect = document.getElementById('storage_filter');
    if (storageSelect) storageSelect.value = '';
    currentStorageType = null;
    currentStorageLocationId = null;

    const statusSelect = document.getElementById('status_filter');
    if (statusSelect) statusSelect.value = '';
    currentFileStatus = null;

    const checksumSelect = document.getElementById('checksum_filter');
    if (checksumSelect) checksumSelect.value = '';
    currentHasChecksum = null;

    const pinnedSelect = document.getElementById('pinned_filter');
    if (pinnedSelect) pinnedSelect.value = '';
    currentIsPinned = null;

    // Reset inputs
    const extensionInput = document.getElementById('extension_filter');
    if (extensionInput) extensionInput.value = '';
    currentExtension = '';

    const mimeInput = document.getElementById('mime_filter');
    if (mimeInput) mimeInput.value = '';
    currentMimeType = '';

    const minSizeInput = document.getElementById('min_size_filter');
    if (minSizeInput) minSizeInput.value = '';
    currentMinSize = null;

    const maxSizeInput = document.getElementById('max_size_filter');
    if (maxSizeInput) maxSizeInput.value = '';
    currentMaxSize = null;

    const minMtimeInput = document.getElementById('min_mtime_filter');
    if (minMtimeInput) minMtimeInput.value = '';
    currentMinMtime = null;

    const maxMtimeInput = document.getElementById('max_mtime_filter');
    if (maxMtimeInput) maxMtimeInput.value = '';
    currentMaxMtime = null;

    // Reset tags
    clearTagFilter();
}

// Load paths for filter dropdown
async function loadPathsForFilter() {
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/paths`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const paths = await response.json();

        const select = document.getElementById('path_id_filter');
        if (select) {
            const urlParams = new URLSearchParams(window.location.search);
            const currentPathId = urlParams.get('path_id');

            select.innerHTML = '<option value="">All Paths</option>' +
                paths.map(p => `<option value="${p.id}" ${currentPathId == p.id ? 'selected' : ''}>${escapeHtml(p.name)}</option>`).join('');

            select.addEventListener('change', function () {
                updateFilters();
            });
        }

        // Setup storage filter
        const storageSelect = document.getElementById('storage_filter');
        if (storageSelect) {
            storageSelect.value = storageFilterValueFromState();

            storageSelect.addEventListener('change', function () {
                updateFilters();
            });
        }

        // Setup status filter
        const statusSelect = document.getElementById('status_filter');
        if (statusSelect) {
            statusSelect.addEventListener('change', function () {
                updateFilters();
            });
        }
    } catch (error) {
        console.error('Error loading paths for filter:', error);
        showNotification(`Failed to load paths: ${error.message}`, 'error');
    }
}

// Fix #10: Persist current filter + sort state to the URL so the view survives a
// page refresh or can be shared as a link.
function syncStateToUrl() {
    const params = new URLSearchParams();
    if (currentPathId) params.set('path_id', currentPathId);
    if (currentStorageLocationId) {
        params.set('storage_location_id', currentStorageLocationId);
    } else if (currentStorageType) {
        params.set('storage_type', currentStorageType);
    }
    if (currentFileStatus) params.set('status', currentFileStatus);
    if (currentSearch) params.set('search', currentSearch);
    if (currentSortBy && currentSortBy !== 'last_seen') params.set('sort_by', currentSortBy);
    if (currentSortOrder && currentSortOrder !== 'desc') params.set('sort_order', currentSortOrder);
    if (currentExtension) params.set('extension', currentExtension);
    if (currentMimeType) params.set('mime_type', currentMimeType);
    if (currentHasChecksum !== null) params.set('has_checksum', currentHasChecksum);
    if (currentIsPinned !== null) params.set('is_pinned', currentIsPinned);
    if (currentMinSize !== null) params.set('min_size', currentMinSize);
    if (currentMaxSize !== null) params.set('max_size', currentMaxSize);
    if (currentTagIds.length > 0) params.set('tag_ids', currentTagIds.join(','));

    const qs = params.toString();
    window.history.replaceState({}, '', qs ? `?${qs}` : window.location.pathname);
}

// Update filters and reload files
function updateFilters() {
    const searchInput = document.getElementById('search_input');
    const pathSelect = document.getElementById('path_id_filter');
    const storageSelect = document.getElementById('storage_filter');
    const statusSelect = document.getElementById('status_filter');
    const tagSelect = document.getElementById('tag_filter');
    const extensionInput = document.getElementById('extension_filter');
    const mimeInput = document.getElementById('mime_filter');
    const checksumSelect = document.getElementById('checksum_filter');
    const pinnedSelect = document.getElementById('pinned_filter');
    const minSizeInput = document.getElementById('min_size_filter');
    const maxSizeInput = document.getElementById('max_size_filter');
    const minMtimeInput = document.getElementById('min_mtime_filter');
    const maxMtimeInput = document.getElementById('max_mtime_filter');

    currentSearch = searchInput ? searchInput.value.trim() : '';
    currentPathId = pathSelect && pathSelect.value ? parseInt(pathSelect.value) : null;
    const storageFilter = parseStorageFilterValue(storageSelect ? storageSelect.value : '');
    currentStorageType = storageFilter.storageType;
    currentStorageLocationId = storageFilter.storageLocationId;
    currentFileStatus = statusSelect && statusSelect.value ? statusSelect.value : null;

    currentExtension = extensionInput ? extensionInput.value.trim() : '';
    currentMimeType = mimeInput ? mimeInput.value.trim() : '';

    currentHasChecksum = checksumSelect && checksumSelect.value ? checksumSelect.value === 'true' : null;
    currentIsPinned = pinnedSelect && pinnedSelect.value ? pinnedSelect.value === 'true' : null;

    currentMinSize = minSizeInput && minSizeInput.value ? parseInt(minSizeInput.value) : null;
    currentMaxSize = maxSizeInput && maxSizeInput.value ? parseInt(maxSizeInput.value) : null;

    // Convert datetime-local values to ISO UTC
    if (minMtimeInput && minMtimeInput.value) {
        const date = new Date(minMtimeInput.value);
        currentMinMtime = !isNaN(date.getTime()) ? date.toISOString() : null;
    } else {
        currentMinMtime = null;
    }

    if (maxMtimeInput && maxMtimeInput.value) {
        const date = new Date(maxMtimeInput.value);
        currentMaxMtime = !isNaN(date.getTime()) ? date.toISOString() : null;
    } else {
        currentMaxMtime = null;
    }

    // Get selected tag IDs from multi-select
    if (tagSelect) {
        const selectedOptions = Array.from(tagSelect.selectedOptions);
        currentTagIds = selectedOptions
            .map(opt => opt.value)
            .filter(val => val !== '')
            .map(val => parseInt(val));
    }

    syncStateToUrl();
    loadFilesList();
}

// Load tags for filter dropdown
async function loadTagsForFilter() {
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/tags`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const tags = await response.json();

        const select = document.getElementById('tag_filter');
        if (select) {
            select.innerHTML = '<option value="">All Tags</option>' +
                tags.map(tag => {
                    const color = tag.color || '#6c757d';
                    return `<option value="${tag.id}" style="background-color: ${color}20;">${escapeHtml(tag.name)} (${tag.file_count})</option>`;
                }).join('');

            select.addEventListener('change', function () {
                updateFilters();
            });
        }
    } catch (error) {
        console.error('Error loading tags for filter:', error);
        showNotification(`Failed to load tags: ${error.message}`, 'error');
    }
}

/**
 * Load storage locations for filter dropdown
 */
async function loadStorageLocationsForFilter() {
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/storage/locations`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const locations = await response.json();

        const select = document.getElementById('storage_filter');
        if (select) {
            const selectedValue = storageFilterValueFromState();
            const locationOptions = locations.map(loc => {
                const backendType = (loc.backend_type || 'local').toUpperCase();
                const label = `${loc.name} (${backendType})`;
                return `<option value="location:${loc.id}">${escapeHtml(label)}</option>`;
            }).join('');

            select.innerHTML = `
                <option value="">All</option>
                <option value="hot">Hot</option>
                <option value="cold">Cold</option>
                ${locations.length ? `<optgroup label="Cold Locations">${locationOptions}</optgroup>` : ''}
            `;

            select.value = selectedValue;
            if (selectedValue && select.value !== selectedValue) {
                select.value = currentStorageType || '';
            }
        }
    } catch (error) {
        console.error('Error loading storage locations for filter:', error);
    }
}

// Clear tag filter
function clearTagFilter() {
    const tagSelect = document.getElementById('tag_filter');
    if (tagSelect) {
        tagSelect.selectedIndex = 0;
        Array.from(tagSelect.options).forEach(opt => opt.selected = false);
    }
    currentTagIds = [];
    loadFilesList();
}

// Show thaw modal
function showThawModal(inventoryId, filePath) {
    const modal = document.getElementById('thawModal');
    if (!modal) return;

    document.getElementById('thawFileName').textContent = filePath;
    document.getElementById('confirmThawBtn').dataset.inventoryId = inventoryId;

    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();
}

// Thaw file action
async function thawFile() {
    const button = document.getElementById('confirmThawBtn');
    if (!button) return;
    if (button.disabled) return;

    const inventoryId = button.dataset.inventoryId;
    const pinCheckbox = document.getElementById('thawPinCheckbox');
    const pin = pinCheckbox ? pinCheckbox.checked : false;

    button.disabled = true;
    const originalHtml = button.innerHTML;
    button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status"></span> Thawing...';

    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/files/thaw/${inventoryId}?pin=${pin}`, {
            method: 'POST'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to thaw file');
        }

        const data = await response.json();
        showNotification('File thawed successfully' + (pin ? ' and pinned' : ''));

        const modal = bootstrap.Modal.getInstance(document.getElementById('thawModal'));
        if (modal) modal.hide();

        loadFilesList();
    } catch (error) {
        console.error('Error thawing file:', error);
        showNotification(`Error thawing file: ${error.message}`, 'error');
    } finally {
        button.disabled = false;
        button.innerHTML = originalHtml;
    }
}

// Toggle pin status for a file
async function togglePin(inventoryId, currentlyPinned) {
    const method = currentlyPinned ? 'DELETE' : 'POST';
    const action = currentlyPinned ? 'unpin' : 'pin';

    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/files/${inventoryId}/pin`, {
            method: method
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || `Failed to ${action} file`);
        }

        const data = await response.json();
        showNotification(data.message);

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

    if (!freezeModal) {
        freezeModal = new bootstrap.Modal(modal);
    }
    freezeModal.show();

    await loadFreezeOptions(inventoryId);
}

// Load freeze options for a file
async function loadFreezeOptions(inventoryId) {
    const selectEl = document.getElementById('freezeLocationSelect');
    const errorEl = document.getElementById('freezeError');
    const confirmBtn = document.getElementById('confirmFreezeBtn');

    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/files/freeze/${inventoryId}/options`);
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to load options');
        }

        const data = await response.json();

        if (!data.can_freeze || data.available_locations.length === 0) {
            selectEl.innerHTML = '<option value="">No storage locations available</option>';
            selectEl.disabled = true;
            confirmBtn.disabled = true;
            if (errorEl) {
                errorEl.textContent = 'No cold storage locations are configured for this path.';
                errorEl.classList.remove('d-none');
            }
        } else {
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
                selectEl.innerHTML = availableLocations.map(loc =>
                    `<option value="${loc.id}" selected>${escapeHtml(loc.name)} (${escapeHtml(loc.path)})</option>`
                ).join('');
                selectEl.disabled = false;
                confirmBtn.disabled = false;
            } else {
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

    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status"></span> Freezing...';
    }

    if (errorEl) {
        errorEl.textContent = '';
        errorEl.classList.add('d-none');
    }

    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/files/freeze/${currentFreezeInventoryId}?storage_location_id=${storageLocationId}&pin=${pin}`, {
            method: 'POST'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to freeze file');
        }

        const data = await response.json();
        showNotification(`File sent to ${data.storage_location.name}` + (pin ? ' and pinned' : ''));

        if (freezeModal) freezeModal.hide();

        loadFilesList();
    } catch (error) {
        console.error('Error freezing file:', error);
        showNotification(`Error freezing file: ${error.message}`, 'error');
        if (errorEl) {
            errorEl.textContent = error.message;
            errorEl.classList.remove('d-none');
        }
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.innerHTML = '<i class="bi bi-snow"></i> Send to Fridge';
        }
    }
}

// Cleanup actions


async function cleanupDuplicates() {
    try {
        let url = `${API_BASE_URL}/cleanup/duplicates`;
        if (currentPathId) {
            url += `?path_id=${currentPathId}`;
        }

        const response = await authenticatedFetch(url, { method: 'POST' });
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

// Tag Management Functions
let currentFileId = null;
let allAvailableTags = [];
let manageTagsModal = null;

// Relocate Modal Management
let relocateModal = null;
let currentRelocateInventoryId = null;

// Remote Migration Modal Management
let remoteMigrationModal = null;
let currentMigrationFileId = null;

// Load all available tags
async function loadAvailableTags() {
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/tags`);
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

    document.getElementById('tagFileName').textContent = filePath;

    if (allAvailableTags.length === 0) {
        await loadAvailableTags();
    }

    const selectEl = document.getElementById('addTagSelect');
    if (selectEl) {
        selectEl.innerHTML = '<option value="">Select a tag...</option>' +
            allAvailableTags.map(tag => `<option value="${tag.id}">${escapeHtml(tag.name)}</option>`).join('');
    }

    await loadFileTags(fileId);

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
        const response = await authenticatedFetch(`${API_BASE_URL}/tags/files/${fileId}/tags`);
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

    if (errorEl) {
        errorEl.textContent = '';
        errorEl.classList.add('d-none');
    }
    if (successEl) {
        successEl.textContent = '';
        successEl.classList.add('d-none');
    }

    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/tags/files/${currentFileId}/tags`, {
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

        if (successEl) {
            successEl.textContent = 'Tag added successfully';
            successEl.classList.remove('d-none');
        }

        if (selectEl) selectEl.value = '';

        await loadFileTags(currentFileId);

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

    if (errorEl) {
        errorEl.textContent = '';
        errorEl.classList.add('d-none');
    }
    if (successEl) {
        successEl.textContent = '';
        successEl.classList.add('d-none');
    }

    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/tags/files/${fileId}/tags/${tagId}`, {
            method: 'DELETE'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to remove tag');
        }

        if (successEl) {
            successEl.textContent = 'Tag removed successfully';
            successEl.classList.remove('d-none');
        }

        await loadFileTags(fileId);

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

    if (!relocateModal) {
        relocateModal = new bootstrap.Modal(modal);
    }
    relocateModal.show();

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
        const response = await authenticatedFetch(`${API_BASE_URL}/files/relocate/${inventoryId}/options`);
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to load options');
        }

        const data = await response.json();

        const currentLocation = data.available_locations.find(loc => loc.is_current);
        if (currentLocation) {
            currentLocationEl.innerHTML = `<span class="badge bg-info me-2">${escapeHtml(currentLocation.name)}</span><small class="text-muted">${escapeHtml(currentLocation.path)}</small>`;
        } else {
            currentLocationEl.innerHTML = '<span class="text-muted">Unknown</span>';
        }

        const targetLocations = data.available_locations.filter(loc => !loc.is_current);

        if (!data.can_relocate || targetLocations.length === 0) {
            selectEl.innerHTML = '<option value="">No other locations available</option>';
            selectEl.disabled = true;
            confirmBtn.disabled = true;
            if (noOptionsEl) noOptionsEl.classList.remove('d-none');
        } else {
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

    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status"></span> Starting...';
    }

    if (errorEl) {
        errorEl.textContent = '';
        errorEl.classList.add('d-none');
    }

    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/files/relocate/${currentRelocateInventoryId}`, {
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

        showNotification(`Relocation started: moving file to ${data.target_location.name}. This will complete in the background.`);

        if (relocateModal) relocateModal.hide();

        loadFilesList();
    } catch (error) {
        console.error('Error starting relocation:', error);
        showNotification(`Error starting relocation: ${error.message}`, 'error');
        if (errorEl) {
            errorEl.textContent = error.message;
            errorEl.classList.remove('d-none');
        }
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.innerHTML = '<i class="bi bi-arrow-right-circle"></i> Relocate File';
        }
    }
}

// Remote Migration Functions

async function showRemoteMigrationModal(fileId, filePath) {
    currentMigrationFileId = fileId;
    const modal = document.getElementById('remoteMigrationModal');
    if (!modal) return;

    document.getElementById('remoteMigrationFileName').textContent = filePath;
    const connSelect = document.getElementById('remoteConnectionSelect');
    connSelect.innerHTML = '<option value="">Loading connections...</option>';
    document.getElementById('remotePathContainer').classList.add('d-none');
    document.getElementById('confirmRemoteMigrationBtn').disabled = true;

    if (!remoteMigrationModal) {
        remoteMigrationModal = new bootstrap.Modal(modal);
    }

    // Reset strategy to COPY
    const copyRadio = document.getElementById('remoteMigrateCopy');
    if (copyRadio) copyRadio.checked = true;

    remoteMigrationModal.show();

    try {
        const response = await authenticatedFetch('/api/v1/remote/connections');
        if (response.ok) {
            const connections = await response.json();
            if (connections.length === 0) {
                connSelect.innerHTML = '<option value="">No remote connections configured</option>';
            } else {
                connSelect.innerHTML = '<option value="">Select a connection...</option>' +
                    connections.map(c => `<option value="${c.id}">${escapeHtml(c.name)} (${c.url})</option>`).join('');
            }
        }
    } catch (error) {
        console.error('Error loading connections:', error);
    }
}

async function onRemoteConnectionChange() {
    const connId = document.getElementById('remoteConnectionSelect').value;
    const pathContainer = document.getElementById('remotePathContainer');
    const pathSelect = document.getElementById('remotePathSelect');
    const confirmBtn = document.getElementById('confirmRemoteMigrationBtn');

    if (!connId) {
        pathContainer.classList.add('d-none');
        confirmBtn.disabled = true;
        return;
    }

    pathContainer.classList.remove('d-none');
    pathSelect.innerHTML = '<option value="">Loading paths...</option>';
    confirmBtn.disabled = true;

    try {
        const response = await authenticatedFetch(`/api/v1/remote/connections/${connId}/paths`);
        if (response.ok) {
            const paths = await response.json();
            if (paths.length === 0) {
                pathSelect.innerHTML = '<option value="">No monitored paths found on remote</option>';
            } else {
                pathSelect.innerHTML = '<option value="">Select a path...</option>' +
                    paths.map(p => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join('');
            }
        }
    } catch (error) {
        console.error('Error loading remote paths:', error);
        pathSelect.innerHTML = '<option value="">Error loading paths</option>';
    }
}

async function startRemoteMigration() {
    const connId = document.getElementById('remoteConnectionSelect').value;
    const pathId = document.getElementById('remotePathSelect').value;
    const confirmBtn = document.getElementById('confirmRemoteMigrationBtn');
    const errorEl = document.getElementById('remoteMigrationError');

    if (!connId || !pathId || !currentMigrationFileId) return;

    confirmBtn.disabled = true;
    confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status"></span> Starting...';
    errorEl.classList.add('d-none');

    const strategy = document.querySelector('input[name="remoteMigrationStrategy"]:checked')?.value || 'COPY';

    try {
        const response = await authenticatedFetch('/api/v1/remote/migrate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                file_inventory_id: currentMigrationFileId,
                remote_connection_id: parseInt(connId),
                remote_monitored_path_id: parseInt(pathId),
                strategy: strategy
            })
        });

        if (response.ok) {
            showNotification('Migration job created successfully. Check Settings > Remote Connections for progress.');
            remoteMigrationModal.hide();
            loadFilesList();
        } else {
            const data = await response.json();
            errorEl.textContent = data.detail || 'Failed to start migration';
            errorEl.classList.remove('d-none');
        }
    } catch (error) {
        console.error('Error starting migration:', error);
        errorEl.textContent = 'Failed to connect to server';
        errorEl.classList.remove('d-none');
    } finally {
        confirmBtn.disabled = false;
        confirmBtn.innerHTML = '<i class="bi bi-send"></i> Start Migration';
    }
}

// ============================================
// Bulk Actions Support
// ============================================

let selectedFiles = [];
let bulkFreezeModal = null;
let bulkAddTagModal = null;
let bulkRemoveTagModal = null;
let bulkRemoteMigrationModal = null;

// Handle selection change
function onSelectionChanged(event) {
    selectedFiles = event.api.getSelectedRows();
    updateBulkActionsToolbar();
}

// Update bulk actions toolbar visibility and state
function updateBulkActionsToolbar() {
    const toolbar = document.getElementById('bulk-actions-toolbar');
    const countEl = document.getElementById('selection-count');

    if (!toolbar) return;

    if (selectedFiles.length > 0) {
        toolbar.style.display = 'flex';
        if (countEl) {
            countEl.textContent = `${selectedFiles.length} file${selectedFiles.length > 1 ? 's' : ''} selected`;
        }

        // Update button states based on selection
        const hotFiles = selectedFiles.filter(f => f.storage_type === 'hot');
        const coldFiles = selectedFiles.filter(f => f.storage_type === 'cold');

        const thawBtn = document.getElementById('bulk-thaw-btn');
        const freezeBtn = document.getElementById('bulk-freeze-btn');

        if (thawBtn) {
            thawBtn.disabled = coldFiles.length === 0;
            thawBtn.title = coldFiles.length === 0 ? 'Select cold storage files to thaw' : `Thaw ${coldFiles.length} file(s)`;
        }
        if (freezeBtn) {
            freezeBtn.disabled = hotFiles.length === 0;
            freezeBtn.title = hotFiles.length === 0 ? 'Select hot storage files to freeze' : `Freeze ${hotFiles.length} file(s)`;
        }

        const migrateBtn = document.getElementById('bulk-migrate-btn');
        if (migrateBtn) {
            migrateBtn.disabled = false;
            migrateBtn.title = `Migrate ${selectedFiles.length} file(s) to remote instance`;
        }
    } else {
        toolbar.style.display = 'none';
    }
}

// Clear selection
function clearSelection() {
    if (gridApi) {
        gridApi.deselectAll();
    }
    selectedFiles = [];
    updateBulkActionsToolbar();
}

// Get selected file IDs
function getSelectedFileIds() {
    return selectedFiles.map(f => f.id);
}

// Show bulk thaw confirmation
function showBulkThawModal() {
    const coldFiles = selectedFiles.filter(f => f.storage_type === 'cold');
    if (coldFiles.length === 0) {
        showNotification('No cold storage files selected', 'warning');
        return;
    }

    const modal = document.getElementById('bulkThawModal');
    if (!modal) return;

    document.getElementById('bulkThawCount').textContent = coldFiles.length;
    document.getElementById('bulkThawPinCheckbox').checked = false;

    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();
}

// Execute bulk thaw
async function executeBulkThaw() {
    const coldFiles = selectedFiles.filter(f => f.storage_type === 'cold');
    const fileIds = coldFiles.map(f => f.id);
    const pinCheckbox = document.getElementById('bulkThawPinCheckbox');
    const pin = pinCheckbox ? pinCheckbox.checked : false;

    const confirmBtn = document.getElementById('confirmBulkThawBtn');
    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Thawing...';
    }

    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/files/bulk/thaw?pin=${pin}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_ids: fileIds })
        });

        if (!response.ok) {
            throw new Error('Bulk thaw request failed');
        }

        const result = await response.json();
        showNotification(`Thawed ${result.successful} of ${result.total} files` +
            (result.failed > 0 ? ` (${result.failed} failed)` : ''));

        bootstrap.Modal.getInstance(document.getElementById('bulkThawModal'))?.hide();
        clearSelection();
        loadFilesList();

    } catch (error) {
        console.error('Bulk thaw error:', error);
        showNotification(`Bulk thaw failed: ${error.message}`, 'error');
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.innerHTML = '<i class="bi bi-fire"></i> Thaw Files';
        }
    }
}

// Show bulk freeze modal
async function showBulkFreezeModal() {
    const hotFiles = selectedFiles.filter(f => f.storage_type === 'hot');
    if (hotFiles.length === 0) {
        showNotification('No hot storage files selected', 'warning');
        return;
    }

    const modal = document.getElementById('bulkFreezeModal');
    if (!modal) return;

    document.getElementById('bulkFreezeCount').textContent = hotFiles.length;
    document.getElementById('bulkFreezeLocationSelect').innerHTML = '<option value="">Loading...</option>';
    document.getElementById('bulkFreezeLocationSelect').disabled = true;
    document.getElementById('confirmBulkFreezeBtn').disabled = true;
    document.getElementById('bulkFreezePinCheckbox').checked = false;

    if (!bulkFreezeModal) {
        bulkFreezeModal = new bootstrap.Modal(modal);
    }
    bulkFreezeModal.show();

    // Load storage locations
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/storage/locations`);
        if (!response.ok) throw new Error('Failed to load storage locations');

        const locations = await response.json();
        const selectEl = document.getElementById('bulkFreezeLocationSelect');

        if (locations.length === 0) {
            selectEl.innerHTML = '<option value="">No storage locations available</option>';
        } else {
            selectEl.innerHTML = '<option value="">Select storage location...</option>' +
                locations.map(loc => `<option value="${loc.id}">${escapeHtml(loc.name)}</option>`).join('');
            selectEl.disabled = false;
        }
    } catch (error) {
        console.error('Error loading storage locations:', error);
        document.getElementById('bulkFreezeLocationSelect').innerHTML =
            '<option value="">Error loading locations</option>';
    }
}

// Handle bulk freeze location change
function onBulkFreezeLocationChange() {
    const selectEl = document.getElementById('bulkFreezeLocationSelect');
    const confirmBtn = document.getElementById('confirmBulkFreezeBtn');
    if (confirmBtn) {
        confirmBtn.disabled = !selectEl || !selectEl.value;
    }
}

// Execute bulk freeze
async function executeBulkFreeze() {
    const hotFiles = selectedFiles.filter(f => f.storage_type === 'hot');
    const fileIds = hotFiles.map(f => f.id);
    const selectEl = document.getElementById('bulkFreezeLocationSelect');
    const storageLocationId = selectEl ? parseInt(selectEl.value) : null;
    const pinCheckbox = document.getElementById('bulkFreezePinCheckbox');
    const pin = pinCheckbox ? pinCheckbox.checked : false;

    if (!storageLocationId) return;

    const confirmBtn = document.getElementById('confirmBulkFreezeBtn');
    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Freezing...';
    }

    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/files/bulk/freeze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                file_ids: fileIds,
                storage_location_id: storageLocationId,
                pin: pin
            })
        });

        if (!response.ok) {
            throw new Error('Bulk freeze request failed');
        }

        const result = await response.json();
        showNotification(`Frozen ${result.successful} of ${result.total} files` +
            (result.failed > 0 ? ` (${result.failed} failed)` : ''));

        bulkFreezeModal?.hide();
        clearSelection();
        loadFilesList();

    } catch (error) {
        console.error('Bulk freeze error:', error);
        showNotification(`Bulk freeze failed: ${error.message}`, 'error');
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.innerHTML = '<i class="bi bi-snow"></i> Freeze Files';
        }
    }
}

// Show bulk add tag modal
async function showBulkAddTagModal() {
    if (selectedFiles.length === 0) return;

    const modal = document.getElementById('bulkAddTagModal');
    if (!modal) return;

    document.getElementById('bulkAddTagCount').textContent = selectedFiles.length;

    // Load tags if needed
    if (allAvailableTags.length === 0) {
        await loadAvailableTags();
    }

    const selectEl = document.getElementById('bulkAddTagSelect');
    if (selectEl) {
        selectEl.innerHTML = '<option value="">Select a tag...</option>' +
            allAvailableTags.map(tag =>
                `<option value="${tag.id}">${escapeHtml(tag.name)}</option>`
            ).join('');
    }

    document.getElementById('confirmBulkAddTagBtn').disabled = true;

    if (!bulkAddTagModal) {
        bulkAddTagModal = new bootstrap.Modal(modal);
    }
    bulkAddTagModal.show();
}

// Handle bulk add tag select change
function onBulkAddTagSelectChange() {
    const selectEl = document.getElementById('bulkAddTagSelect');
    const confirmBtn = document.getElementById('confirmBulkAddTagBtn');
    if (confirmBtn) {
        confirmBtn.disabled = !selectEl || !selectEl.value;
    }
}

// Execute bulk add tag
async function executeBulkAddTag() {
    const fileIds = getSelectedFileIds();
    const selectEl = document.getElementById('bulkAddTagSelect');
    const tagId = selectEl ? parseInt(selectEl.value) : null;

    if (!tagId) return;

    const confirmBtn = document.getElementById('confirmBulkAddTagBtn');
    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Adding...';
    }

    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/tags/bulk/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_ids: fileIds, tag_id: tagId })
        });

        if (!response.ok) {
            throw new Error('Bulk add tag request failed');
        }

        const result = await response.json();
        showNotification(`Added tag to ${result.successful} of ${result.total} files` +
            (result.failed > 0 ? ` (${result.failed} failed)` : ''));

        bulkAddTagModal?.hide();
        clearSelection();
        loadFilesList();

    } catch (error) {
        console.error('Bulk add tag error:', error);
        showNotification(`Bulk add tag failed: ${error.message}`, 'error');
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.innerHTML = '<i class="bi bi-tag"></i> Add Tag';
        }
    }
}

// Show bulk remove tag modal
async function showBulkRemoveTagModal() {
    if (selectedFiles.length === 0) return;

    const modal = document.getElementById('bulkRemoveTagModal');
    if (!modal) return;

    document.getElementById('bulkRemoveTagCount').textContent = selectedFiles.length;

    // Load tags if needed
    if (allAvailableTags.length === 0) {
        await loadAvailableTags();
    }

    const selectEl = document.getElementById('bulkRemoveTagSelect');
    if (selectEl) {
        selectEl.innerHTML = '<option value="">Select a tag...</option>' +
            allAvailableTags.map(tag =>
                `<option value="${tag.id}">${escapeHtml(tag.name)}</option>`
            ).join('');
    }

    document.getElementById('confirmBulkRemoveTagBtn').disabled = true;

    if (!bulkRemoveTagModal) {
        bulkRemoveTagModal = new bootstrap.Modal(modal);
    }
    bulkRemoveTagModal.show();
}

// Handle bulk remove tag select change
function onBulkRemoveTagSelectChange() {
    const selectEl = document.getElementById('bulkRemoveTagSelect');
    const confirmBtn = document.getElementById('confirmBulkRemoveTagBtn');
    if (confirmBtn) {
        confirmBtn.disabled = !selectEl || !selectEl.value;
    }
}

// Execute bulk remove tag
async function executeBulkRemoveTag() {
    const fileIds = getSelectedFileIds();
    const selectEl = document.getElementById('bulkRemoveTagSelect');
    const tagId = selectEl ? parseInt(selectEl.value) : null;

    if (!tagId) return;

    const confirmBtn = document.getElementById('confirmBulkRemoveTagBtn');
    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Removing...';
    }

    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/tags/bulk/remove`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_ids: fileIds, tag_id: tagId })
        });

        if (!response.ok) {
            throw new Error('Bulk remove tag request failed');
        }

        const result = await response.json();
        showNotification(`Removed tag from ${result.successful} of ${result.total} files` +
            (result.failed > 0 ? ` (${result.failed} failed)` : ''));

        bulkRemoveTagModal?.hide();
        clearSelection();
        loadFilesList();

    } catch (error) {
        console.error('Bulk remove tag error:', error);
        showNotification(`Bulk remove tag failed: ${error.message}`, 'error');
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.innerHTML = '<i class="bi bi-tag-x"></i> Remove Tag';
        }
    }
}

// Show bulk remote migration modal
async function showBulkRemoteMigrationModal() {
    if (selectedFiles.length === 0) {
        showNotification('No files selected', 'warning');
        return;
    }

    const modal = document.getElementById('bulkRemoteMigrationModal');
    if (!modal) return;

    document.getElementById('bulkRemoteMigrationCount').textContent = selectedFiles.length;
    const connSelect = document.getElementById('bulkRemoteConnectionSelect');
    connSelect.innerHTML = '<option value="">Loading connections...</option>';
    document.getElementById('bulkRemotePathContainer').classList.add('d-none');
    document.getElementById('confirmBulkRemoteMigrationBtn').disabled = true;
    document.getElementById('bulkRemoteMigrationError').classList.add('d-none');

    if (!bulkRemoteMigrationModal) {
        bulkRemoteMigrationModal = new bootstrap.Modal(modal);
    }

    // Reset strategy to COPY
    const copyRadio = document.getElementById('bulkRemoteMigrateCopy');
    if (copyRadio) copyRadio.checked = true;

    bulkRemoteMigrationModal.show();

    try {
        const response = await authenticatedFetch('/api/v1/remote/connections');
        if (response.ok) {
            const connections = await response.json();
            if (connections.length === 0) {
                connSelect.innerHTML = '<option value="">No remote connections configured</option>';
            } else {
                connSelect.innerHTML = '<option value="">Select a connection...</option>' +
                    connections.map(c => `<option value="${c.id}">${escapeHtml(c.name)} (${c.url})</option>`).join('');
            }
        }
    } catch (error) {
        console.error('Error loading connections:', error);
        connSelect.innerHTML = '<option value="">Error loading connections</option>';
    }
}

// Handle bulk remote connection change
async function onBulkRemoteConnectionChange() {
    const connId = document.getElementById('bulkRemoteConnectionSelect').value;
    const pathContainer = document.getElementById('bulkRemotePathContainer');
    const pathSelect = document.getElementById('bulkRemotePathSelect');
    const confirmBtn = document.getElementById('confirmBulkRemoteMigrationBtn');
    const errorDiv = document.getElementById('bulkRemoteMigrationError');

    errorDiv.classList.add('d-none');

    if (!connId) {
        pathContainer.classList.add('d-none');
        confirmBtn.disabled = true;
        return;
    }

    pathContainer.classList.remove('d-none');
    pathSelect.innerHTML = '<option value="">Loading paths...</option>';
    confirmBtn.disabled = true;

    try {
        const response = await authenticatedFetch(`/api/v1/remote/connections/${connId}/paths`);
        if (response.ok) {
            const paths = await response.json();
            if (paths.length === 0) {
                pathSelect.innerHTML = '<option value="">No paths available</option>';
            } else {
                pathSelect.innerHTML = '<option value="">Select a path...</option>' +
                    paths.map(p => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join('');
            }
        } else {
            throw new Error('Failed to load remote paths');
        }
    } catch (error) {
        console.error('Error loading remote paths:', error);
        pathSelect.innerHTML = '<option value="">Error loading paths</option>';
        errorDiv.textContent = 'Failed to load remote paths: ' + error.message;
        errorDiv.classList.remove('d-none');
    }
}

// Handle bulk remote path change
function onBulkRemotePathChange() {
    const pathSelect = document.getElementById('bulkRemotePathSelect');
    const confirmBtn = document.getElementById('confirmBulkRemoteMigrationBtn');
    if (confirmBtn) {
        confirmBtn.disabled = !pathSelect || !pathSelect.value;
    }
}

// Execute bulk remote migration
async function executeBulkRemoteMigration() {
    const fileIds = getSelectedFileIds();
    const connId = document.getElementById('bulkRemoteConnectionSelect').value;
    const pathId = document.getElementById('bulkRemotePathSelect').value;

    if (!connId || !pathId) return;

    const confirmBtn = document.getElementById('confirmBulkRemoteMigrationBtn');
    const errorDiv = document.getElementById('bulkRemoteMigrationError');

    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Starting...';
    }

    errorDiv.classList.add('d-none');

    const strategy = document.querySelector('input[name="bulkRemoteMigrationStrategy"]:checked')?.value || 'COPY';

    try {
        const response = await authenticatedFetch(`/api/v1/remote/migrate/bulk`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                file_ids: fileIds,
                remote_connection_id: parseInt(connId),
                remote_monitored_path_id: parseInt(pathId),
                strategy: strategy
            })
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
            throw new Error(errorData.detail || 'Bulk migration request failed');
        }

        const result = await response.json();
        showNotification(`Created ${result.successful} migration jobs` +
            (result.failed > 0 ? ` (${result.failed} failed)` : ''));

        bulkRemoteMigrationModal?.hide();
        clearSelection();
        loadFilesList();

    } catch (error) {
        console.error('Bulk migration error:', error);
        errorDiv.textContent = error.message;
        errorDiv.classList.remove('d-none');
        showNotification(`Bulk migration failed: ${error.message}`, 'error');
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.innerHTML = '<i class="bi bi-send"></i> Start Migration';
        }
    }
}

// Execute bulk pin
async function executeBulkPin() {
    const fileIds = getSelectedFileIds();

    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/files/bulk/pin`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_ids: fileIds })
        });

        if (!response.ok) {
            throw new Error('Bulk pin request failed');
        }

        const result = await response.json();
        showNotification(`Pinned ${result.successful} of ${result.total} files` +
            (result.failed > 0 ? ` (${result.failed} failed)` : ''));

        clearSelection();
        loadFilesList();

    } catch (error) {
        console.error('Bulk pin error:', error);
        showNotification(`Bulk pin failed: ${error.message}`, 'error');
    }
}

// Execute bulk unpin
async function executeBulkUnpin() {
    const fileIds = getSelectedFileIds();

    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/files/bulk/unpin`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_ids: fileIds })
        });

        if (!response.ok) {
            throw new Error('Bulk unpin request failed');
        }

        const result = await response.json();
        showNotification(`Unpinned ${result.successful} of ${result.total} files` +
            (result.failed > 0 ? ` (${result.failed} failed)` : ''));

        clearSelection();
        loadFilesList();

    } catch (error) {
        console.error('Bulk unpin error:', error);
        showNotification(`Bulk unpin failed: ${error.message}`, 'error');
    }
}

// ============================================
// Initialization
// ============================================

let filesPageInitialized = false;

async function initFilesPage() {
    if (filesPageInitialized) {
        return;
    }

    await waitForAgGrid();
    filesPageInitialized = true;

    // Fix #10: Restore full filter + sort state from URL params so a shared link or
    // page refresh lands back in the same view.
    const urlParams = new URLSearchParams(window.location.search);
    currentPathId = urlParams.get('path_id') ? parseInt(urlParams.get('path_id')) : null;
    currentStorageType = urlParams.get('storage_type') || null;
    currentStorageLocationId = urlParams.get('storage_location_id')
        ? parseInt(urlParams.get('storage_location_id'), 10)
        : null;
    if (currentStorageLocationId !== null && !Number.isNaN(currentStorageLocationId)) {
        currentStorageType = 'cold';
    } else {
        currentStorageLocationId = null;
    }
    currentFileStatus = urlParams.get('status') || null;
    currentSortBy = urlParams.get('sort_by') || 'last_seen';
    currentSortOrder = urlParams.get('sort_order') || 'desc';
    currentSearch = urlParams.get('search') || '';
    currentExtension = urlParams.get('extension') || '';
    currentMimeType = urlParams.get('mime_type') || '';
    const hasChecksumParam = urlParams.get('has_checksum');
    currentHasChecksum = hasChecksumParam !== null ? hasChecksumParam === 'true' : null;
    const isPinnedParam = urlParams.get('is_pinned');
    currentIsPinned = isPinnedParam !== null ? isPinnedParam === 'true' : null;
    currentMinSize = urlParams.get('min_size') ? parseInt(urlParams.get('min_size')) : null;
    currentMaxSize = urlParams.get('max_size') ? parseInt(urlParams.get('max_size')) : null;
    const tagIdsParam = urlParams.get('tag_ids');
    currentTagIds = tagIdsParam ? tagIdsParam.split(',').map(Number).filter(Boolean) : [];

    // Sync URL-restored values to visible form controls
    const statusSelect = document.getElementById('status_filter');
    if (statusSelect && currentFileStatus) {
        statusSelect.value = currentFileStatus;
    }
    const searchInput = document.getElementById('search_input');
    if (searchInput && currentSearch) {
        searchInput.value = currentSearch;
    }

    // Initialize AG Grid
    initGrid();

    loadFilesList();
    loadPathsForFilter();
    loadTagsForFilter();
    loadStorageLocationsForFilter();

    // Setup event handlers
    const confirmBtn = document.getElementById('confirmThawBtn');
    if (confirmBtn) {
        confirmBtn.addEventListener('click', thawFile);
    }

    const searchInputEl = document.getElementById('search_input');
    if (searchInputEl) {
        // Fix #5: Debounced input handler for type-as-you-search (400 ms delay).
        // The Enter key / Search button still trigger immediately.
        const debouncedSearch = debounce(updateFilters, 400);
        searchInputEl.addEventListener('input', debouncedSearch);
        searchInputEl.addEventListener('keypress', function (e) {
            if (e.key === 'Enter') {
                updateFilters();
            }
        });
    }

    const searchBtn = document.getElementById('search_btn');
    if (searchBtn) {
        searchBtn.addEventListener('click', updateFilters);
    }

    const clearBtn = document.getElementById('clear_filters_btn');
    if (clearBtn) {
        clearBtn.addEventListener('click', resetFilters);
    }

    // Advanced filters listeners
    const extensionInput = document.getElementById('extension_filter');
    if (extensionInput) {
        extensionInput.addEventListener('keypress', function (e) {
            if (e.key === 'Enter') updateFilters();
        });
        extensionInput.addEventListener('blur', updateFilters);
    }

    const mimeInput = document.getElementById('mime_filter');
    if (mimeInput) {
        mimeInput.addEventListener('keypress', function (e) {
            if (e.key === 'Enter') updateFilters();
        });
        mimeInput.addEventListener('blur', updateFilters);
    }

    const checksumSelect = document.getElementById('checksum_filter');
    if (checksumSelect) {
        checksumSelect.addEventListener('change', updateFilters);
    }

    const pinnedSelect = document.getElementById('pinned_filter');
    if (pinnedSelect) {
        pinnedSelect.addEventListener('change', updateFilters);
    }

    const locationIdSelect = document.getElementById('location_id_filter');
    if (locationIdSelect) {
        locationIdSelect.addEventListener('change', updateFilters);
    }

    const minSizeFilter = document.getElementById('min_size_filter');
    if (minSizeFilter) {
        minSizeFilter.addEventListener('keypress', function (e) {
            if (e.key === 'Enter') updateFilters();
        });
        minSizeFilter.addEventListener('blur', updateFilters);
    }

    const maxSizeFilter = document.getElementById('max_size_filter');
    if (maxSizeFilter) {
        maxSizeFilter.addEventListener('keypress', function (e) {
            if (e.key === 'Enter') updateFilters();
        });
        maxSizeFilter.addEventListener('blur', updateFilters);
    }

    const minMtimeFilter = document.getElementById('min_mtime_filter');
    if (minMtimeFilter) {
        minMtimeFilter.addEventListener('change', updateFilters);
    }

    const maxMtimeFilter = document.getElementById('max_mtime_filter');
    if (maxMtimeFilter) {
        maxMtimeFilter.addEventListener('change', updateFilters);
    }

    const addTagSelect = document.getElementById('addTagSelect');
    if (addTagSelect) {
        addTagSelect.addEventListener('change', function () {
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

    const clearTagFilterBtn = document.getElementById('clear_tag_filter_btn');
    if (clearTagFilterBtn) {
        clearTagFilterBtn.addEventListener('click', clearTagFilter);
    }

    const relocateTargetSelect = document.getElementById('relocateTargetSelect');
    if (relocateTargetSelect) {
        relocateTargetSelect.addEventListener('change', onRelocateTargetChange);
    }

    const confirmRelocateBtn = document.getElementById('confirmRelocateBtn');
    if (confirmRelocateBtn) {
        confirmRelocateBtn.addEventListener('click', relocateFile);
    }

    const freezeLocationSelect = document.getElementById('freezeLocationSelect');
    if (freezeLocationSelect) {
        freezeLocationSelect.addEventListener('change', onFreezeLocationChange);
    }

    const confirmFreezeBtn = document.getElementById('confirmFreezeBtn');
    if (confirmFreezeBtn) {
        confirmFreezeBtn.addEventListener('click', freezeFile);
    }

    const remoteConnectionSelect = document.getElementById('remoteConnectionSelect');
    if (remoteConnectionSelect) {
        remoteConnectionSelect.addEventListener('change', onRemoteConnectionChange);
    }

    const remotePathSelect = document.getElementById('remotePathSelect');
    if (remotePathSelect) {
        remotePathSelect.addEventListener('change', function () {
            const confirmBtn = document.getElementById('confirmRemoteMigrationBtn');
            if (confirmBtn) confirmBtn.disabled = !this.value;
        });
    }

    const confirmRemoteMigrationBtn = document.getElementById('confirmRemoteMigrationBtn');
    if (confirmRemoteMigrationBtn) {
        confirmRemoteMigrationBtn.addEventListener('click', startRemoteMigration);
    }

    const thawModal = document.getElementById('thawModal');
    if (thawModal) {
        thawModal.addEventListener('show.bs.modal', function () {
            const tempRadio = document.getElementById('pinTemp');
            if (tempRadio) tempRadio.checked = true;
        });
    }

    // Bulk action event handlers
    const bulkThawBtn = document.getElementById('bulk-thaw-btn');
    if (bulkThawBtn) {
        bulkThawBtn.addEventListener('click', showBulkThawModal);
    }

    const bulkFreezeBtn = document.getElementById('bulk-freeze-btn');
    if (bulkFreezeBtn) {
        bulkFreezeBtn.addEventListener('click', showBulkFreezeModal);
    }

    const bulkMigrateBtn = document.getElementById('bulk-migrate-btn');
    if (bulkMigrateBtn) {
        bulkMigrateBtn.addEventListener('click', showBulkRemoteMigrationModal);
    }

    const bulkAddTagBtn = document.getElementById('bulk-add-tag-btn');
    if (bulkAddTagBtn) {
        bulkAddTagBtn.addEventListener('click', showBulkAddTagModal);
    }

    const bulkRemoveTagBtn = document.getElementById('bulk-remove-tag-btn');
    if (bulkRemoveTagBtn) {
        bulkRemoveTagBtn.addEventListener('click', showBulkRemoveTagModal);
    }

    const bulkPinBtn = document.getElementById('bulk-pin-btn');
    if (bulkPinBtn) {
        bulkPinBtn.addEventListener('click', executeBulkPin);
    }

    const bulkUnpinBtn = document.getElementById('bulk-unpin-btn');
    if (bulkUnpinBtn) {
        bulkUnpinBtn.addEventListener('click', executeBulkUnpin);
    }

    const clearSelectionBtn = document.getElementById('clear-selection-btn');
    if (clearSelectionBtn) {
        clearSelectionBtn.addEventListener('click', clearSelection);
    }

    // Bulk modal confirm buttons
    const confirmBulkThawBtn = document.getElementById('confirmBulkThawBtn');
    if (confirmBulkThawBtn) {
        confirmBulkThawBtn.addEventListener('click', executeBulkThaw);
    }

    const confirmBulkFreezeBtn = document.getElementById('confirmBulkFreezeBtn');
    if (confirmBulkFreezeBtn) {
        confirmBulkFreezeBtn.addEventListener('click', executeBulkFreeze);
    }

    const bulkFreezeLocationSelect = document.getElementById('bulkFreezeLocationSelect');
    if (bulkFreezeLocationSelect) {
        bulkFreezeLocationSelect.addEventListener('change', onBulkFreezeLocationChange);
    }

    const confirmBulkAddTagBtn = document.getElementById('confirmBulkAddTagBtn');
    if (confirmBulkAddTagBtn) {
        confirmBulkAddTagBtn.addEventListener('click', executeBulkAddTag);
    }

    const bulkAddTagSelect = document.getElementById('bulkAddTagSelect');
    if (bulkAddTagSelect) {
        bulkAddTagSelect.addEventListener('change', onBulkAddTagSelectChange);
    }

    const confirmBulkRemoveTagBtn = document.getElementById('confirmBulkRemoveTagBtn');
    if (confirmBulkRemoveTagBtn) {
        confirmBulkRemoveTagBtn.addEventListener('click', executeBulkRemoveTag);
    }

    const bulkRemoveTagSelect = document.getElementById('bulkRemoveTagSelect');
    if (bulkRemoveTagSelect) {
        bulkRemoveTagSelect.addEventListener('change', onBulkRemoveTagSelectChange);
    }

    const confirmBulkRemoteMigrationBtn = document.getElementById('confirmBulkRemoteMigrationBtn');
    if (confirmBulkRemoteMigrationBtn) {
        confirmBulkRemoteMigrationBtn.addEventListener('click', executeBulkRemoteMigration);
    }

    const bulkRemoteConnectionSelect = document.getElementById('bulkRemoteConnectionSelect');
    if (bulkRemoteConnectionSelect) {
        bulkRemoteConnectionSelect.addEventListener('change', onBulkRemoteConnectionChange);
    }

    const bulkRemotePathSelect = document.getElementById('bulkRemotePathSelect');
    if (bulkRemotePathSelect) {
        bulkRemotePathSelect.addEventListener('change', onBulkRemotePathChange);
    }
}

window.runWhenFileFridgeReady(() => {
    void initFilesPage().catch(error => {
        console.error('Failed to initialize files page:', error);
    });
});

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (currentAbortController) {
        currentAbortController.abort();
    }
    if (gridApi) {
        gridApi.destroy();
    }
});
