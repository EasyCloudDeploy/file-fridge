/**
 * Storage Locations Management JavaScript
 */

let allLocations = [];
let deleteModal = null;

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', async () => {
    // Initialize modal
    deleteModal = new bootstrap.Modal(document.getElementById('deleteLocationModal'));

    // Load storage locations
    await loadStorageLocations();
});

/**
 * Load all storage locations from the API
 */
async function loadStorageLocations() {
    const loadingEl = document.getElementById('locations-loading');
    const contentEl = document.getElementById('locations-content');
    const noLocationsEl = document.getElementById('no-locations-message');

    try {
        loadingEl.style.display = 'block';
        contentEl.style.display = 'none';
        noLocationsEl.style.display = 'none';

        const response = await authenticatedFetch(`/api/v1/storage/locations`);
        if (!response.ok) {
            throw new Error(`Failed to load storage locations: ${response.statusText}`);
        }

        allLocations = await response.json();

        loadingEl.style.display = 'none';

        if (allLocations.length === 0) {
            noLocationsEl.style.display = 'block';
        } else {
            contentEl.style.display = 'block';
            renderStorageLocations();
        }
    } catch (error) {
        console.error('Error loading storage locations:', error);
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
            <td><strong>${escapeHtml(location.name)}</strong></td>
            <td><code>${escapeHtml(location.path)}</code></td>
            <td>
                <span class="badge bg-secondary">${location.path_count} paths</span>
            </td>
            <td>
                ${location.is_encrypted
            ? `<span class="badge bg-success" title="Encryption Status: ${location.encryption_status}">
                        <i class="bi bi-lock-fill"></i> ${location.encryption_status}
                       </span>`
            : '<span class="badge bg-light text-dark"><i class="bi bi-unlock"></i> Off</span>'
        }
            </td>
            <td><small class="text-muted">${formatDateTime(location.created_at)}</small></td>
            <td>
                <div class="btn-group btn-group-sm" role="group">
                    <button type="button" class="btn btn-outline-info" onclick="triggerFreeze(${location.id}, '${escapeHtml(location.name)}')" title="Freeze: Scan all associated paths" ${location.path_count === 0 ? 'disabled' : ''}>
                        <i class="bi bi-snow"></i> Freeze
                    </button>
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
 * Trigger "Freeze" (scan) for all paths associated with this storage location
 */
async function triggerFreeze(locationId, locationName) {
    if (!confirm(`This will trigger a scan for all paths using "${locationName}" to move eligible files to cold storage. Proceed?`)) {
        return;
    }

    try {
        // We need to fetch paths to know which ones use this location
        const response = await authenticatedFetch('/api/v1/paths');
        if (!response.ok) throw new Error('Failed to load paths');

        const paths = await response.json();
        const associatedPaths = paths.filter(p =>
            p.storage_locations && p.storage_locations.some(loc => loc.id === locationId)
        );

        if (associatedPaths.length === 0) {
            showAlert('warning', `No active paths are currently using "${locationName}".`);
            return;
        }

        let started = 0;
        let failed = 0;

        for (const path of associatedPaths) {
            try {
                const scanResponse = await authenticatedFetch(`/api/v1/paths/${path.id}/scan`, {
                    method: 'POST'
                });
                if (scanResponse.ok) {
                    started++;
                } else {
                    failed++;
                }
            } catch (err) {
                console.error(`Error triggering scan for path ${path.id}:`, err);
                failed++;
            }
        }

        if (started > 0) {
            showAlert('success', `Started "Freeze" scan for ${started} path(s)${failed > 0 ? ` (${failed} failed)` : ''}.`);
        } else if (failed > 0) {
            showAlert('danger', `Failed to start scans for ${failed} path(s).`);
        }

    } catch (error) {
        console.error('Error in triggerFreeze:', error);
        showAlert('danger', `Error initiating freeze: ${error.message}`);
    }
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
