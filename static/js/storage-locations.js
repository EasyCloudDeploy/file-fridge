/**
 * Storage Locations Management JavaScript
 */
const assertRequiredElements = (...args) => window.assertRequiredElements(...args);
const authenticatedFetch = (...args) => window.authenticatedFetch(...args);
const setRegionState = (...args) => window.setRegionState(...args);

const LOCATIONS_REGION = { loading: '#locations-loading', content: '#locations-content', empty: '#no-locations-message' };

let allLocations = [];
let deleteModal = null;
let storageLocationsInitialized = false;

async function initStorageLocationsPage() {
    if (storageLocationsInitialized) {
        return;
    }
    storageLocationsInitialized = true;

    assertRequiredElements(
        ['locations-loading', 'locations-content', 'no-locations-message', 'locationsTable'],
        'storage locations page'
    );

    // Initialize modal
    deleteModal = new bootstrap.Modal(document.getElementById('deleteLocationModal'));

    // Load storage locations
    await loadStorageLocations();
}

window.runWhenFileFridgeReady(() => {
    void initStorageLocationsPage();
});

/**
 * Load all storage locations from the API
 */
async function loadStorageLocations() {
    try {
        setRegionState({ ...LOCATIONS_REGION, state: 'loading' });

        const response = await authenticatedFetch(`/api/v1/storage/locations`);
        if (!response.ok) {
            throw new Error(`Failed to load storage locations: ${response.statusText}`);
        }

        allLocations = await response.json();

        if (allLocations.length === 0) {
            setRegionState({ ...LOCATIONS_REGION, state: 'empty' });
        } else {
            setRegionState({ ...LOCATIONS_REGION, state: 'content' });
            renderStorageLocations();
        }
    } catch (error) {
        console.error('Error loading storage locations:', error);
        setRegionState({ ...LOCATIONS_REGION, state: 'error', errorMessage: `Failed to load storage locations: ${error.message}` });
        showAlert('danger', `Failed to load storage locations: ${error.message}`);
    }
}

/**
 * Render storage locations table
 */
function renderStorageLocations() {
    const tbody = document.querySelector('#locationsTable tbody');

    tbody.innerHTML = allLocations.map(location => `
        <tr>
            <td>
                <strong>${escapeHtml(location.name)}</strong>
                <br><span class="badge bg-info mt-1">${escapeHtml(location.backend_type || 'local')}</span>
                <span class="badge bg-secondary mt-1">${escapeHtml(location.operation_mode || 'move')}</span>
                ${location.allow_offline ? `<span class="badge bg-primary mt-1">Offline Allowed</span>` : ''}
                ${location.backend_type === 'local' && location.local_drive_is_removable ? `
                    <span class="badge bg-dark mt-1">Removable</span>` : ''}
                ${location.backend_type === 'local' && location.local_drive_is_removable ? `
                    <span class="badge ${location.local_drive_is_connected ? 'bg-success' : 'bg-warning text-dark'} mt-1">
                        ${location.local_drive_is_connected ? 'Drive Connected' : 'Drive Offline'}
                    </span>` : ''}
                ${location.permissions_error ? `
                    <br><span class="badge bg-danger mt-1" title="${escapeHtml(location.permissions_error)}">
                        <i class="bi bi-shield-exclamation"></i> Permission Error
                    </span>` : ''}
            </td>
            <td>
                <code>${escapeHtml(location.path)}</code>
                ${location.backend_type === 'local' && (location.local_drive_label || location.local_drive_identifier) ? `
                    <br><small class="text-muted">Drive: ${escapeHtml(location.local_drive_label || 'Unnamed')} (${escapeHtml(location.local_drive_identifier || 'unknown-id')})</small>` : ''}
                ${location.permissions_error ? `
                    <br><small class="text-danger"><i class="bi bi-exclamation-triangle-fill"></i> ${escapeHtml(location.permissions_error)}</small>` : ''}
            </td>
            <td>
                <span class="badge bg-secondary">${location.path_count} paths</span>
            </td>
            <td><small class="text-muted">${formatDateTime(location.created_at)}</small></td>
            <td>
                <div class="btn-group btn-group-sm" role="group">
                    <a href="/storage-locations/${location.id}/edit" class="btn btn-outline-primary">
                        <i class="bi bi-pencil"></i> Edit
                    </a>
                    <button type="button" class="btn btn-outline-danger" onclick="showDeleteModal(${location.id}, '${escapeHtml(location.name)}')">
                        <i class="bi bi-trash"></i> Delete
                    </button>
                </div>
            </td>
        </tr>
    `).join('');
}

/**
 * Show delete confirmation modal
 */
function showDeleteModal(id, name) {
    document.getElementById('location-name-to-delete').textContent = name;
    
    const confirmBtn = document.getElementById('confirm-delete-button');
    const forceCheckbox = document.getElementById('forceDeleteCheckbox');

    // Clone and replace the button to remove old event listeners
    const newConfirmBtn = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);

    newConfirmBtn.addEventListener('click', async () => {
        await deleteLocation(id, name, forceCheckbox.checked);
    });

    forceCheckbox.checked = false; // Reset checkbox
    deleteModal.show();
}

/**
 * Delete a storage location
 */
async function deleteLocation(id, name, isForced) {
    deleteModal.hide();

    let url = `/api/v1/storage/locations/${id}`;
    if (isForced) {
        url += '?force=true';
    }

    try {
        const response = await authenticatedFetch(url, {
            method: 'DELETE'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || `Failed to delete storage location: ${response.statusText}`);
        }

        // Reload locations
        await loadStorageLocations();

        showAlert('success', `Storage location "${name}" deleted successfully.`);
    } catch (error) {
        console.error('Error deleting storage location:', error);
        showAlert('danger', error.message);
    }
}

/**
 * Format date/time for display
 */
function formatDateTime(dateString) {
    if (!dateString) return 'N/A';
    const date = new Date(dateString);
    return date.toLocaleString();
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(unsafe) {
    if (!unsafe) return '';
    return unsafe
        .toString()
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

/**
 * Show alert message
 */
function showAlert(type, message) {
    const alertContainer = document.getElementById('alert-container');
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} alert-dismissible fade show`;
    alertDiv.role = 'alert';
    alertDiv.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    `;

    alertContainer.appendChild(alertDiv);

    // Auto-dismiss after 5 seconds
    setTimeout(() => {
        alertDiv.remove();
    }, 5000);
}
