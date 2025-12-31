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
                    row.innerHTML = `
                        <td><strong>${escapeHtml(path.name)}</strong></td>
                        <td><code>${escapeHtml(path.source_path)}</code></td>
                        <td><code>${escapeHtml(path.cold_storage_path)}</code></td>
                        <td><span class="badge bg-info">${escapeHtml(path.operation_type)}</span></td>
                        <td>${Math.floor(path.check_interval_seconds / 60)} min</td>
                        <td>
                            <span class="badge bg-${path.enabled ? 'success' : 'secondary'}">
                                ${path.enabled ? 'Enabled' : 'Disabled'}
                            </span>
                        </td>
                        <td>
                            <div class="btn-group btn-group-sm">
                                <a href="/paths/${path.id}" class="btn btn-outline-primary" title="View">
                                    <i class="bi bi-eye"></i>
                                </a>
                                <a href="/paths/${path.id}/edit" class="btn btn-outline-secondary" title="Edit">
                                    <i class="bi bi-pencil"></i>
                                </a>
                                <button type="button" class="btn btn-outline-info" onclick="triggerScan(${path.id})" title="Scan Now">
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
        
        // Render path details
        const pathNameEl = document.getElementById('path-name');
        if (pathNameEl) pathNameEl.textContent = path.name;
        
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
                    </td>
                </tr>
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
        if (scanBtn) scanBtn.onclick = () => triggerScan(pathId);
        
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
    if (!confirm('Are you sure you want to delete this path?')) return;
    
    try {
        const response = await fetch(`${API_BASE_URL}/paths/${pathId}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            showFlashMessage('Path deleted successfully');
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

async function triggerScan(pathId) {
    try {
        const response = await fetch(`${API_BASE_URL}/paths/${pathId}/scan`, {
            method: 'POST'
        });
        
        if (response.ok) {
            const result = await response.json();
            showFlashMessage(result.message || 'Scan triggered successfully');
        } else {
            const error = await response.json();
            showFlashMessage(error.detail || 'Failed to trigger scan', 'danger');
        }
    } catch (error) {
        console.error('Error triggering scan:', error);
        showFlashMessage(`Error triggering scan: ${error.message}`, 'danger');
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
            enabled: formData.get('enabled') === 'on'
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

