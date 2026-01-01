// Paths management JavaScript - client-side rendering
const API_BASE_URL = '/api/v1';

// Utility functions
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
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

// Load and render paths list
async function loadPathsList() {
    const loadingEl = document.getElementById('paths-loading');
    const contentEl = document.getElementById('paths-content');
    const emptyEl = document.getElementById('no-paths-message');
    const tableBody = document.querySelector('#pathsTable tbody');
    
    if (loadingEl) loadingEl.style.display = 'block';
    if (contentEl) contentEl.style.display = 'none';
    if (emptyEl) emptyEl.style.display = 'none';
    if (tableBody) tableBody.innerHTML = '';
    
    try {
        const response = await fetch(`${API_BASE_URL}/paths`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const paths = await response.json();
        
        if (paths.length === 0) {
            if (emptyEl) emptyEl.style.display = 'block';
        } else {
            if (tableBody) {
                paths.forEach(path => {
                    const row = tableBody.insertRow();
                    if (path.error_message) {
                        row.classList.add('table-danger');
                    }
                    row.innerHTML = `
                        <td>
                            <strong>${escapeHtml(path.name)}</strong>
                            ${path.error_message ? `
                                <div class="alert alert-danger alert-sm mt-1 mb-0 py-1 px-2" role="alert">
                                    <i class="bi bi-exclamation-triangle-fill"></i> <strong>Error:</strong> ${escapeHtml(path.error_message)}
                                </div>
                            ` : ''}
                        </td>
                        <td><code>${escapeHtml(path.source_path)}</code></td>
                        <td><code>${escapeHtml(path.cold_storage_path)}</code></td>
                        <td><span class="badge bg-info">${escapeHtml(path.operation_type)}</span></td>
                        <td>${Math.floor(path.check_interval_seconds / 60)} min</td>
                        <td>
                            <span class="badge bg-${path.enabled ? 'success' : 'secondary'}">
                                ${path.enabled ? 'Enabled' : 'Disabled'}
                            </span>
                            ${path.error_message ? `
                                <br><span class="badge bg-danger mt-1">
                                    <i class="bi bi-exclamation-triangle-fill"></i> Error State
                                </span>
                            ` : ''}
                        </td>
                        <td>
                            <div class="btn-group btn-group-sm">
                                <a href="/paths/${path.id}" class="btn btn-outline-primary" title="View">
                                    <i class="bi bi-eye"></i>
                                </a>
                                <a href="/paths/${path.id}/edit" class="btn btn-outline-secondary" title="Edit">
                                    <i class="bi bi-pencil"></i>
                                </a>
                                <button type="button" class="btn btn-outline-info scan-btn-${path.id}" onclick="triggerScan(${path.id}, this)" title="Scan Now" ${path.error_message ? 'disabled' : ''}>
                                    <i class="bi bi-arrow-repeat"></i>
                                </button>
                                <button type="button" class="btn btn-outline-danger" onclick="deletePath(${path.id})" title="Delete">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </div>
                        </td>
                    `;
                });
            }
            if (contentEl) contentEl.style.display = 'block';
        }
    } catch (error) {
        console.error('Error loading paths:', error);
        showFlashMessage(`Failed to load paths: ${error.message}`, 'danger');
        if (emptyEl) emptyEl.style.display = 'block';
    } finally {
        if (loadingEl) loadingEl.style.display = 'none';
    }
}

// Load and render path detail
async function loadPathDetail(pathId) {
    const loadingEl = document.getElementById('path-loading');
    const contentEl = document.getElementById('path-content');
    const errorEl = document.getElementById('path-error');
    
    if (loadingEl) loadingEl.style.display = 'block';
    if (contentEl) contentEl.style.display = 'none';
    if (errorEl) errorEl.style.display = 'none';
    
    try {
        const [pathResponse, criteriaResponse] = await Promise.all([
            fetch(`${API_BASE_URL}/paths/${pathId}`),
            fetch(`${API_BASE_URL}/criteria/path/${pathId}`)
        ]);
        
        if (!pathResponse.ok) {
            if (pathResponse.status === 404) {
                window.location.href = '/paths';
                return;
            }
            throw new Error(`HTTP error! status: ${pathResponse.status}`);
        }
        
        const path = await pathResponse.json();
        const criteria = await criteriaResponse.ok ? await criteriaResponse.json() : [];
        
        // Update page title
        document.title = `${path.name} - File Fridge`;
        
        // Show error state if present
        if (path.error_message) {
            const errorAlert = document.createElement('div');
            errorAlert.className = 'alert alert-danger alert-dismissible fade show';
            errorAlert.setAttribute('role', 'alert');
            errorAlert.innerHTML = `
                <h5 class="alert-heading">
                    <i class="bi bi-exclamation-triangle-fill"></i> Path in Error State
                </h5>
                <p class="mb-0"><strong>This path is not processing files due to a configuration error:</strong></p>
                <p class="mb-2">${escapeHtml(path.error_message)}</p>
                <hr>
                <p class="mb-0"><small>Please fix the configuration issue to resume file processing. You can edit the path or criteria to resolve this.</small></p>
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            `;
            const mainContent = document.querySelector('main.container-fluid');
            if (mainContent) {
                mainContent.insertBefore(errorAlert, mainContent.firstChild);
            }
        }
        
        // Render path details
        const pathNameEl = document.getElementById('path-name');
        if (pathNameEl) {
            pathNameEl.innerHTML = `
                ${escapeHtml(path.name)}
                ${path.error_message ? `
                    <span class="badge bg-danger ms-2">
                        <i class="bi bi-exclamation-triangle-fill"></i> Error State
                    </span>
                ` : ''}
            `;
        }
        
        const pathConfigBody = document.querySelector('#pathConfigTable tbody');
        if (pathConfigBody) {
            pathConfigBody.innerHTML = `
                <tr>
                    <th width="40%">Name:</th>
                    <td><strong>${escapeHtml(path.name)}</strong></td>
                </tr>
                <tr>
                    <th>Source Path:</th>
                    <td><code>${escapeHtml(path.source_path)}</code></td>
                </tr>
                <tr>
                    <th>Cold Storage:</th>
                    <td><code>${escapeHtml(path.cold_storage_path)}</code></td>
                </tr>
                <tr>
                    <th>Operation Type:</th>
                    <td><span class="badge bg-info">${escapeHtml(path.operation_type)}</span></td>
                </tr>
                <tr>
                    <th>Check Interval:</th>
                    <td>${Math.floor(path.check_interval_seconds / 60)} minutes</td>
                </tr>
                <tr>
                    <th>Status:</th>
                    <td>
                        <span class="badge bg-${path.enabled ? 'success' : 'secondary'}">
                            ${path.enabled ? 'Enabled' : 'Disabled'}
                        </span>
                        ${path.error_message ? `
                            <span class="badge bg-danger ms-2">
                                <i class="bi bi-exclamation-triangle-fill"></i> Error State
                            </span>
                        ` : ''}
                    </td>
                </tr>
                ${path.error_message ? `
                    <tr class="table-danger">
                        <th>Error Message:</th>
                        <td>
                            <div class="alert alert-danger mb-0 py-2" role="alert">
                                <i class="bi bi-exclamation-triangle-fill"></i> ${escapeHtml(path.error_message)}
                            </div>
                        </td>
                    </tr>
                ` : ''}
                <tr>
                    <th>Created:</th>
                    <td>${new Date(path.created_at).toLocaleString()}</td>
                </tr>
            `;
        }
        
        // Render criteria
        const criteriaTableBody = document.querySelector('#criteriaTable tbody');
        if (criteriaTableBody) {
            if (criteria.length === 0) {
                criteriaTableBody.innerHTML = `
                    <tr>
                        <td colspan="5" class="text-center text-muted">
                            No criteria configured. Files will match all criteria (move all files).
                            <br><a href="/paths/${pathId}/criteria/new" class="btn btn-sm btn-primary mt-2">
                                <i class="bi bi-plus"></i> Add Your First Criterion
                            </a>
                        </td>
                    </tr>
                `;
            } else {
                criteriaTableBody.innerHTML = criteria.map(c => `
                    <tr>
                        <td><code>${escapeHtml(c.criterion_type)}</code></td>
                        <td><code>${escapeHtml(c.operator)}</code></td>
                        <td><code>${escapeHtml(c.value)}</code></td>
                        <td>
                            <span class="badge bg-${c.enabled ? 'success' : 'secondary'}">
                                ${c.enabled ? 'Enabled' : 'Disabled'}
                            </span>
                        </td>
                        <td>
                            <div class="btn-group btn-group-sm">
                                <a href="/criteria/${c.id}/edit" class="btn btn-outline-secondary" title="Edit">
                                    <i class="bi bi-pencil"></i>
                                </a>
                                <button type="button" class="btn btn-outline-danger" onclick="deleteCriteria(${c.id}, ${pathId})" title="Delete">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </div>
                        </td>
                    </tr>
                `).join('');
            }
        }
        
        // Update edit, scan, and add criteria buttons
        const editBtn = document.getElementById('edit-path-btn');
        if (editBtn) editBtn.href = `/paths/${pathId}/edit`;
        
        const scanBtn = document.getElementById('scan-path-btn');
        if (scanBtn) {
            scanBtn.onclick = (e) => triggerScan(pathId, e.currentTarget);
            if (path.error_message) {
                scanBtn.disabled = true;
                scanBtn.title = 'Cannot scan: Path is in error state';
            }
        }
        
        const addCriteriaBtn = document.getElementById('add-criteria-btn');
        if (addCriteriaBtn) addCriteriaBtn.href = `/paths/${pathId}/criteria/new`;
        
        const viewFilesBtn = document.getElementById('view-files-btn');
        if (viewFilesBtn) viewFilesBtn.href = `/files?path_id=${pathId}`;
        
        if (contentEl) contentEl.style.display = 'block';
    } catch (error) {
        console.error('Error loading path:', error);
        if (errorEl) {
            errorEl.textContent = `Failed to load path: ${error.message}`;
            errorEl.style.display = 'block';
        }
    } finally {
        if (loadingEl) loadingEl.style.display = 'none';
    }
}

// Path actions
async function deletePath(pathId) {
    // Ask if user wants to undo operations
    const undoOps = confirm(
        'Do you want to move all files back from cold storage before deleting?\n\n' +
        'Click OK to undo operations (move files back)\n' +
        'Click Cancel to delete without moving files back'
    );
    
    if (!confirm(`Are you sure you want to delete this path?${undoOps ? '\n\nAll files will be moved back from cold storage.' : ''}`)) {
        return;
    }
    
    try {
        const response = await fetch(`${API_BASE_URL}/paths/${pathId}?undo_operations=${undoOps}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            const result = await response.json();
            let message = 'Path deleted successfully';
            if (undoOps && result.files_reversed > 0) {
                message += `. ${result.files_reversed} file(s) moved back from cold storage.`;
            }
            if (result.errors && result.errors.length > 0) {
                message += ` ${result.errors.length} error(s) occurred.`;
            }
            showFlashMessage(message, result.errors && result.errors.length > 0 ? 'warning' : 'success');
            window.location.href = '/paths';
        } else {
            const error = await response.json();
            showFlashMessage(error.detail || 'Failed to delete path', 'danger');
        }
    } catch (error) {
        console.error('Error deleting path:', error);
        showFlashMessage(`Error deleting path: ${error.message}`, 'danger');
    }
}

async function triggerScan(pathId, buttonElement = null) {
    // Find the button element if not provided
    if (!buttonElement) {
        buttonElement = event ? event.currentTarget : document.getElementById('scan-path-btn');
    }

    // Save original button content
    const originalContent = buttonElement ? buttonElement.innerHTML : null;

    try {
        // Update button to show loading state
        if (buttonElement) {
            buttonElement.disabled = true;
            buttonElement.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Scanning...';
        }

        const response = await fetch(`${API_BASE_URL}/paths/${pathId}/scan`, {
            method: 'POST'
        });

        if (response.ok) {
            const result = await response.json();

            // Build detailed success message
            let message = 'Scan completed successfully';
            if (result.total_scanned !== undefined) {
                // Detailed scan results available
                const parts = [`Scanned ${result.total_scanned} file(s)`];

                if (result.files_moved !== undefined && result.files_moved > 0) {
                    parts.push(`moved ${result.files_moved}`);
                }

                if (result.files_skipped !== undefined && result.files_skipped > 0) {
                    parts.push(`${result.files_skipped} already correctly placed`);
                }

                message = `Scan complete: ${parts.join(', ')}`;
            } else if (result.files_found !== undefined && result.files_moved !== undefined) {
                message = `Scan completed: Found ${result.files_found} file(s), moved ${result.files_moved} file(s)`;
            } else if (result.message) {
                message = result.message;
            }

            showFlashMessage(message, 'success');

            // Reload path detail if on detail page
            if (window.location.pathname.match(/^\/paths\/\d+$/)) {
                setTimeout(() => loadPathDetail(pathId), 1000);
            } else {
                // Reload paths list if on list page
                setTimeout(() => loadPathsList(), 1000);
            }
        } else {
            const error = await response.json();
            showFlashMessage(error.detail || 'Failed to trigger scan', 'danger');
        }
    } catch (error) {
        console.error('Error triggering scan:', error);
        showFlashMessage(`Error triggering scan: ${error.message}`, 'danger');
    } finally {
        // Restore button to original state
        if (buttonElement && originalContent) {
            buttonElement.disabled = false;
            buttonElement.innerHTML = originalContent;
        }
    }
}

async function deleteCriteria(criteriaId, pathId) {
    if (!confirm('Are you sure you want to delete this criterion?')) return;
    
    try {
        const response = await fetch(`${API_BASE_URL}/criteria/${criteriaId}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            showFlashMessage('Criterion deleted successfully');
            loadPathDetail(pathId);
        } else {
            const error = await response.json();
            showFlashMessage(error.detail || 'Failed to delete criterion', 'danger');
        }
    } catch (error) {
        console.error('Error deleting criterion:', error);
        showFlashMessage(`Error deleting criterion: ${error.message}`, 'danger');
    }
}

// Form submission handlers
function setupPathForm() {
    const form = document.getElementById('path-form');
    if (!form) return;
    
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const formData = new FormData(form);
        const pathData = {
            name: formData.get('name'),
            source_path: formData.get('source_path'),
            cold_storage_path: formData.get('cold_storage_path'),
            operation_type: formData.get('operation_type'),
            check_interval_seconds: parseInt(formData.get('check_interval_seconds')),
            enabled: formData.get('enabled') === 'on',
            prevent_indexing: formData.get('prevent_indexing') === 'on'
        };
        
        const pathId = form.dataset.pathId;
        const url = pathId ? `${API_BASE_URL}/paths/${pathId}` : `${API_BASE_URL}/paths`;
        const method = pathId ? 'PUT' : 'POST';
        
        try {
            const response = await fetch(url, {
                method: method,
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(pathData)
            });
            
            if (response.ok) {
                const path = await response.json();
                showFlashMessage(`Path ${pathId ? 'updated' : 'created'} successfully`);
                window.location.href = `/paths/${path.id}`;
            } else {
                const error = await response.json();
                showFlashMessage(error.detail || `Failed to ${pathId ? 'update' : 'create'} path`, 'danger');
            }
        } catch (error) {
            console.error('Error saving path:', error);
            showFlashMessage(`Error saving path: ${error.message}`, 'danger');
        }
    });
}

function setupCriteriaForm() {
    const form = document.getElementById('criteria-form');
    if (!form) return;
    
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const formData = new FormData(form);
        const criteriaData = {
            criterion_type: formData.get('criterion_type'),
            operator: formData.get('operator'),
            value: formData.get('value'),
            enabled: formData.get('enabled') === 'on'
        };
        
        const criteriaId = form.dataset.criteriaId;
        const pathId = form.dataset.pathId;
        
        if (!pathId) {
            showFlashMessage('Path ID is required', 'danger');
            return;
        }
        
        const url = criteriaId ? `${API_BASE_URL}/criteria/${criteriaId}` : `${API_BASE_URL}/criteria/path/${pathId}`;
        const method = criteriaId ? 'PUT' : 'POST';
        
        try {
            const response = await fetch(url, {
                method: method,
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(criteriaData)
            });
            
            if (response.ok) {
                showFlashMessage(`Criterion ${criteriaId ? 'updated' : 'created'} successfully`);
                window.location.href = `/paths/${pathId}`;
            } else {
                const error = await response.json();
                showFlashMessage(error.detail || `Failed to ${criteriaId ? 'update' : 'create'} criterion`, 'danger');
            }
        } catch (error) {
            console.error('Error saving criterion:', error);
            showFlashMessage(`Error saving criterion: ${error.message}`, 'danger');
        }
    });
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    // Check which page we're on and load appropriate data
    if (window.location.pathname === '/paths' || window.location.pathname === '/paths/') {
        loadPathsList();
    } else if (window.location.pathname.match(/^\/paths\/\d+$/)) {
        const pathId = parseInt(window.location.pathname.split('/')[2]);
        loadPathDetail(pathId);
    } else if (window.location.pathname.match(/^\/paths\/\d+\/edit$/) || window.location.pathname === '/paths/new') {
        setupPathForm();
        // If editing, load path data
        if (window.location.pathname.includes('/edit')) {
            const pathId = parseInt(window.location.pathname.split('/')[2]);
            loadPathForEdit(pathId);
            // Update form title
            document.getElementById('form-title').innerHTML = '<i class="bi bi-pencil"></i> Edit Monitored Path';
            document.getElementById('submit-text').textContent = 'Update';
        }
    } else if (window.location.pathname.match(/^\/paths\/\d+\/criteria\/new$/)) {
        setupCriteriaForm();
        // Extract path_id from URL
        const pathId = parseInt(window.location.pathname.split('/')[2]);
        loadPathForCriteriaForm(pathId);
    } else if (window.location.pathname.match(/^\/criteria\/\d+\/edit$/)) {
        setupCriteriaForm();
        // If editing, load criteria data
        const criteriaId = parseInt(window.location.pathname.split('/')[2]);
        loadCriteriaForEdit(criteriaId);
        // Update form title
        document.getElementById('form-title').innerHTML = '<i class="bi bi-pencil"></i> Edit Criteria';
        document.getElementById('submit-text').textContent = 'Update';
    }
});

// Load path data for edit form
async function loadPathForEdit(pathId) {
    try {
        const response = await fetch(`${API_BASE_URL}/paths/${pathId}`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const path = await response.json();
        
        // Populate form
        document.getElementById('name').value = path.name;
        document.getElementById('source_path').value = path.source_path;
        document.getElementById('cold_storage_path').value = path.cold_storage_path;
        document.getElementById('operation_type').value = path.operation_type;
        document.getElementById('check_interval_seconds').value = path.check_interval_seconds;
        document.getElementById('enabled').checked = path.enabled;
        document.getElementById('prevent_indexing').checked = path.prevent_indexing;
        
        // Set form data attribute
        const form = document.getElementById('path-form');
        if (form) form.dataset.pathId = pathId;
    } catch (error) {
        console.error('Error loading path for edit:', error);
        showFlashMessage(`Failed to load path: ${error.message}`, 'danger');
    }
}

// Load path info for criteria form
async function loadPathForCriteriaForm(pathId) {
    try {
        const response = await fetch(`${API_BASE_URL}/paths/${pathId}`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const path = await response.json();
        
        // Update path info display
        document.getElementById('path-name').textContent = path.name;
        document.getElementById('path-source').textContent = path.source_path;
        
        // Set form data attribute
        const form = document.getElementById('criteria-form');
        if (form) {
            form.dataset.pathId = pathId;
        }
        
        // Update back and cancel links
        const backLink = document.getElementById('back-link');
        const cancelLink = document.getElementById('cancel-link');
        if (backLink) backLink.href = `/paths/${pathId}`;
        if (cancelLink) cancelLink.href = `/paths/${pathId}`;
    } catch (error) {
        console.error('Error loading path for criteria form:', error);
        showFlashMessage(`Failed to load path: ${error.message}`, 'danger');
    }
}

// Load criteria data for edit form
async function loadCriteriaForEdit(criteriaId) {
    try {
        const response = await fetch(`${API_BASE_URL}/criteria/${criteriaId}`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const criterion = await response.json();
        
        // Get path ID from criterion
        const pathId = criterion.path_id;
        
        // Load path info
        const pathResponse = await fetch(`${API_BASE_URL}/paths/${pathId}`);
        if (pathResponse.ok) {
            const path = await pathResponse.json();
            document.getElementById('path-name').textContent = path.name;
            document.getElementById('path-source').textContent = path.source_path;
        }
        
        // Populate form
        document.getElementById('criterion_type').value = criterion.criterion_type;
        document.getElementById('operator').value = criterion.operator;
        document.getElementById('value').value = criterion.value;
        document.getElementById('enabled').checked = criterion.enabled;
        
        // Set form data attributes
        const form = document.getElementById('criteria-form');
        if (form) {
            form.dataset.criteriaId = criteriaId;
            form.dataset.pathId = pathId;
        }
        
        // Update back and cancel links
        const backLink = document.getElementById('back-link');
        const cancelLink = document.getElementById('cancel-link');
        if (backLink) backLink.href = `/paths/${pathId}`;
        if (cancelLink) cancelLink.href = `/paths/${pathId}`;
        
        // Trigger help text display
        const typeSelect = document.getElementById('criterion_type');
        if (typeSelect) typeSelect.dispatchEvent(new Event('change'));
    } catch (error) {
        console.error('Error loading criterion for edit:', error);
        showFlashMessage(`Failed to load criterion: ${error.message}`, 'danger');
    }
}

