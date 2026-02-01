/**
 * Storage Location Form JavaScript
 */

let locationId = null;
let isEditMode = false;

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', async () => {
    // Check if we're in edit mode
    const urlParts = window.location.pathname.split('/');
    if (urlParts.includes('edit')) {
        isEditMode = true;
        locationId = parseInt(urlParts[urlParts.length - 2]);
        await loadLocation();
    }

    // Set up form submission
    document.getElementById('storage-location-form').addEventListener('submit', handleSubmit);
});

/**
 * Load location data for editing
 */
async function loadLocation() {
    try {
        const response = await authenticatedFetch(`/api/v1/storage/locations/${locationId}`);
        if (!response.ok) {
            throw new Error(`Failed to load storage location: ${response.statusText}`);
        }

        const location = await response.json();

        // Update form title
        document.getElementById('form-title').innerHTML = '<i class="bi bi-pencil"></i> Edit Storage Location';
        document.getElementById('submit-text').textContent = 'Save Changes';

        // Populate form fields
        document.getElementById('name').value = location.name;
        document.getElementById('path').value = location.path;
        document.getElementById('caution_threshold_percent').value = location.caution_threshold_percent || 20;
        document.getElementById('critical_threshold_percent').value = location.critical_threshold_percent || 10;
        document.getElementById('is_encrypted').checked = location.is_encrypted || false;

    } catch (error) {
        console.error('Error loading storage location:', error);
        showAlert('danger', error.message);
    }
}

/**
 * Handle form submission
 */
async function handleSubmit(event) {
    event.preventDefault();

    const form = event.target;
    const submitBtn = document.getElementById('submit-btn');
    const originalText = document.getElementById('submit-text').textContent;

    // Disable submit button
    submitBtn.disabled = true;
    document.getElementById('submit-text').textContent = 'Saving...';

    try {
        const formData = {
            name: document.getElementById('name').value.trim(),
            path: document.getElementById('path').value.trim(),
            caution_threshold_percent: parseInt(document.getElementById('caution_threshold_percent').value),
            critical_threshold_percent: parseInt(document.getElementById('critical_threshold_percent').value),
            is_encrypted: document.getElementById('is_encrypted').checked
        };

        const url = isEditMode
            ? `/api/v1/storage/locations/${locationId}`
            : '/api/v1/storage/locations';

        const method = isEditMode ? 'PUT' : 'POST';

        const response = await authenticatedFetch(url, {
            method: method,
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(formData)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || `Failed to save storage location: ${response.statusText}`);
        }

        // Redirect to storage locations list
        window.location.href = '/storage-locations';

    } catch (error) {
        console.error('Error saving storage location:', error);
        showAlert('danger', error.message);

        // Re-enable submit button
        submitBtn.disabled = false;
        document.getElementById('submit-text').textContent = originalText;
    }
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

    const container = document.querySelector('.card-body');
    container.insertBefore(alertDiv, container.firstChild);

    // Auto-dismiss after 5 seconds
    setTimeout(() => {
        alertDiv.remove();
    }, 5000);
}
