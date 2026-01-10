/**
 * Storage Locations Management JavaScript
 */

let allLocations = [];

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', async () => {
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

        const response = await fetch(`/api/v1/storage/locations`);
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
        loadingEl.innerHTML = `
            <div class="alert alert-danger" role="alert">
                <i class="bi bi-exclamation-triangle"></i>
                Failed to load storage locations: ${error.message}
            </div>
        `;
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
                <span class="badge bg-secondary">0 paths</span>
            </td>
            <td><small class="text-muted">${formatDateTime(location.created_at)}</small></td>
            <td>
                <div class="btn-group btn-group-sm" role="group">
                    <a href="/storage-locations/${location.id}/edit" class="btn btn-outline-primary">
                        <i class="bi bi-pencil"></i> Edit
                    </a>
                    <button type="button" class="btn btn-outline-danger" onclick="deleteLocation(${location.id}, '${escapeHtml(location.name)}')">
                        <i class="bi bi-trash"></i> Delete
                    </button>
                </div>
            </td>
        </tr>
    `).join('');
}

/**
 * Delete a storage location
 */
async function deleteLocation(id, name) {
    if (!confirm(`Are you sure you want to delete storage location "${name}"?\n\nThis will fail if the location is still associated with any monitored paths.`)) {
        return;
    }

    try {
        const response = await fetch(`/api/v1/storage/locations/${id}`, {
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
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} alert-dismissible fade show`;
    alertDiv.role = 'alert';
    alertDiv.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    `;

    const container = document.querySelector('main .container-fluid');
    container.insertBefore(alertDiv, container.firstChild);

    // Auto-dismiss after 5 seconds
    setTimeout(() => {
        alertDiv.remove();
    }, 5000);
}
