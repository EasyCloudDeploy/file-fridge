// Remote Files browser JavaScript
const API_BASE_URL = '/api/v1';

// State
let gridApi = null;
let connectionId = null;
let remotePaths = [];
let localPaths = [];

// Filter state
let currentPathId = null;
let currentSearch = '';
let currentStorageType = '';
let skip = 0;
let limit = 1000; // Larger limit since we don't have infinite scroll yet

// AG Grid Column Definitions
const columnDefs = [
    {
        field: 'file_path',
        headerName: 'Remote File Path',
        flex: 2,
        minWidth: 200,
        cellRenderer: params => `<code class="file-path-cell">${escapeHtml(params.value)}</code>`,
        tooltipField: 'file_path'
    },
    {
        field: 'storage_type',
        headerName: 'Storage',
        width: 100,
        cellRenderer: params => {
            const type = params.value.toLowerCase();
            const badgeClass = type === 'hot' ? 'bg-success' : 'bg-info';
            return `<span class="badge ${badgeClass}">${type.toUpperCase()}</span>`;
        }
    },
    {
        field: 'file_size',
        headerName: 'Size',
        width: 110,
        valueFormatter: params => formatBytes(params.value)
    },
    {
        field: 'file_mtime',
        headerName: 'Modified',
        width: 160,
        valueFormatter: params => formatDate(params.value)
    },
    {
        headerName: 'Actions',
        width: 120,
        pinned: 'right',
        cellRenderer: params => {
            const file = params.data;
            return `
                <button type="button" class="btn btn-sm btn-success" 
                        onclick="showPullModal(${file.inventory_id}, '${escapeHtml(file.file_path)}')"
                        title="Pull file to local storage">
                    <i class="bi bi-cloud-download"></i> Pull
                </button>
            `;
        }
    }
];

// AG Grid Options
const gridOptions = {
    columnDefs: columnDefs,
    rowData: [],
    defaultColDef: {
        resizable: true,
        sortable: true,
        filter: true
    },
    animateRows: true,
    rowHeight: 45,
    overlayLoadingTemplate: '<span class="spinner-border spinner-border-sm text-primary"></span> Fetching remote files...',
    overlayNoRowsTemplate: '<span class="text-muted">No files found for this remote path</span>'
};

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
    connectionId = document.getElementById('connection-id').value;
    if (!connectionId) {
        console.error('Connection ID not found');
        return;
    }

    // Init AG Grid
    const gridDiv = document.getElementById('filesGrid');
    agGrid.createGrid(gridDiv, gridOptions);
    gridApi = gridOptions.api;

    // Load initial data
    await Promise.all([
        loadConnectionDetails(),
        loadRemotePaths(),
        loadLocalPaths()
    ]);

    // Setup event listeners
    document.getElementById('path_id_filter').addEventListener('change', (e) => {
        currentPathId = e.target.value;
        loadRemoteFiles();
    });

    document.getElementById('storage_filter').addEventListener('change', (e) => {
        currentStorageType = e.target.value;
        loadRemoteFiles();
    });

    document.getElementById('search_btn').addEventListener('click', () => {
        currentSearch = document.getElementById('search_input').value;
        loadRemoteFiles();
    });

    document.getElementById('search_input').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            currentSearch = e.target.value;
            loadRemoteFiles();
        }
    });

    document.getElementById('confirmPullBtn').addEventListener('click', handlePull);

    document.getElementById('localPathSelect').addEventListener('change', (e) => {
        document.getElementById('confirmPullBtn').disabled = !e.target.value;
    });
});

async function loadConnectionDetails() {
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/remote/connections/${connectionId}`);
        if (response.ok) {
            const conn = await response.json();
            document.getElementById('remote-instance-name').textContent = conn.name;
        }
    } catch (error) {
        console.error('Error loading connection details:', error);
    }
}

async function loadRemotePaths() {
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/remote/connections/${connectionId}/paths`);
        if (response.ok) {
            remotePaths = await response.json();
            const select = document.getElementById('path_id_filter');
            remotePaths.forEach(path => {
                const opt = document.createElement('option');
                opt.value = path.id;
                opt.textContent = `${path.name} (${path.source_path})`;
                select.appendChild(opt);
            });

            // Auto-select first path if available
            if (remotePaths.length > 0) {
                select.value = remotePaths[0].id;
                currentPathId = remotePaths[0].id;
                await loadRemoteFiles();
            }
        }
    } catch (error) {
        console.error('Error loading remote paths:', error);
    }
}

async function loadLocalPaths() {
    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/paths/monitored`);
        if (response.ok) {
            localPaths = await response.json();
            const select = document.getElementById('localPathSelect');
            localPaths.forEach(path => {
                const opt = document.createElement('option');
                opt.value = path.id;
                opt.textContent = `${path.name} (${path.source_path})`;
                select.appendChild(opt);
            });
        }
    } catch (error) {
        console.error('Error loading local paths:', error);
    }
}

async function loadRemoteFiles() {
    if (!currentPathId) return;

    const loadingEl = document.getElementById('files-loading');
    const gridEl = document.getElementById('filesGrid');
    const noFilesEl = document.getElementById('no-files-message');

    loadingEl.style.display = 'block';
    gridEl.style.display = 'none';
    noFilesEl.style.display = 'none';

    try {
        const params = new URLSearchParams({
            path_id: currentPathId,
            skip: skip,
            limit: limit
        });
        if (currentSearch) params.append('search', currentSearch);
        if (currentStorageType) params.append('storage_type', currentStorageType);

        const response = await authenticatedFetch(`${API_BASE_URL}/remote/connections/${connectionId}/browse-files?${params.toString()}`);
        if (!response.ok) throw new Error('Failed to fetch remote files');

        const data = await response.json();

        if (data.files && data.files.length > 0) {
            gridOptions.api.setGridOption('rowData', data.files);
            gridEl.style.display = 'block';
        } else {
            noFilesEl.style.display = 'block';
        }
    } catch (error) {
        console.error('Error loading remote files:', error);
        showToast('Error loading remote files: ' + error.message, 'danger');
    } finally {
        loadingEl.style.display = 'none';
    }
}

// Global scope for the onclick handler
window.showPullModal = (inventoryId, filePath) => {
    document.getElementById('pullFileName').textContent = filePath;
    document.getElementById('pullFileName').dataset.inventoryId = inventoryId;

    // Reset modal
    document.getElementById('localPathSelect').value = '';
    document.getElementById('confirmPullBtn').disabled = true;
    document.getElementById('pullError').classList.add('d-none');

    // Reset strategy to COPY
    const copyRadio = document.getElementById('pullCopy');
    if (copyRadio) copyRadio.checked = true;

    const modal = new bootstrap.Modal(document.getElementById('pullModal'));
    modal.show();
};

async function handlePull() {
    const inventoryId = document.getElementById('pullFileName').dataset.inventoryId;
    const localPathId = document.getElementById('localPathSelect').value;
    const btn = document.getElementById('confirmPullBtn');
    const errorEl = document.getElementById('pullError');

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Starting...';
    errorEl.classList.add('d-none');

    const strategy = document.querySelector('input[name="pullStrategy"]:checked')?.value || 'COPY';

    try {
        const response = await authenticatedFetch(`${API_BASE_URL}/remote/pull`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                remote_connection_id: parseInt(connectionId),
                remote_file_inventory_id: parseInt(inventoryId),
                local_monitored_path_id: parseInt(localPathId),
                strategy: strategy
            })
        });

        if (response.ok) {
            const result = await response.json();
            showToast('Pull job created successfully! Check Active Transfers in Settings.', 'success');
            bootstrap.Modal.getInstance(document.getElementById('pullModal')).hide();
        } else {
            const err = await response.json();
            errorEl.textContent = 'Error: ' + (err.detail || 'Failed to start pull');
            errorEl.classList.remove('d-none');
        }
    } catch (error) {
        errorEl.textContent = 'Network error: ' + error.message;
        errorEl.classList.remove('d-none');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-download"></i> Start Pull';
    }
}

// Helpers
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
    if (!dateString) return 'N/A';
    const date = new Date(dateString);
    return date.toLocaleString();
}
