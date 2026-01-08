/**
 * Notifier Management JavaScript
 */

let allNotifiers = [];
let currentEditingNotifierId = null;
let notifierModal = null;
let deleteModal = null;

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', async () => {
    // Initialize Bootstrap modals
    notifierModal = new bootstrap.Modal(document.getElementById('notifier_modal'));
    deleteModal = new bootstrap.Modal(document.getElementById('delete_modal'));

    // Set up event listeners
    setupEventListeners();

    // Load app info
    await loadAppInfo();

    // Load notifiers
    await loadNotifiers();
});

/**
 * Load application info (name and version)
 */
async function loadAppInfo() {
    try {
        const response = await fetch('/health');
        if (response.ok) {
            const data = await response.json();
            document.getElementById('app-name-navbar').textContent = data.app_name || 'File Fridge';
            document.getElementById('app-version').textContent = data.version || 'Unknown';
        }
    } catch (error) {
        console.error('Failed to load app info:', error);
    }
}

/**
 * Set up all event listeners
 */
function setupEventListeners() {
    // Create notifier button
    document.getElementById('create_notifier_btn').addEventListener('click', () => {
        openNotifierModal();
    });

    // Save notifier button
    document.getElementById('save_notifier_btn').addEventListener('click', () => {
        saveNotifier();
    });

    // Confirm delete button
    document.getElementById('confirm_delete_btn').addEventListener('click', () => {
        confirmDelete();
    });

    // Notifier type change - update placeholder and show/hide SMTP config
    document.getElementById('notifier_type').addEventListener('change', (e) => {
        const addressInput = document.getElementById('notifier_address');
        const addressHelp = document.getElementById('address_help');
        const smtpSection = document.getElementById('smtp_config_section');

        if (e.target.value === 'email') {
            addressInput.placeholder = 'admin@example.com';
            addressInput.type = 'email';
            addressHelp.textContent = 'Email address to receive notifications';
            smtpSection.style.display = 'block';
        } else if (e.target.value === 'generic_webhook') {
            addressInput.placeholder = 'https://hooks.example.com/webhook';
            addressInput.type = 'url';
            addressHelp.textContent = 'Webhook URL to POST notifications to';
            smtpSection.style.display = 'none';
        } else {
            addressInput.placeholder = 'Email address or webhook URL';
            addressInput.type = 'text';
            addressHelp.textContent = 'Email address or webhook URL';
            smtpSection.style.display = 'none';
        }
    });

    // Form submission with Enter key
    document.getElementById('notifier_form').addEventListener('submit', (e) => {
        e.preventDefault();
        saveNotifier();
    });
}

/**
 * Load all notifiers from the API
 */
async function loadNotifiers() {
    const loadingEl = document.getElementById('notifiers_loading');
    const contentEl = document.getElementById('notifiers_content');

    try {
        loadingEl.style.display = 'block';
        contentEl.style.display = 'none';

        const response = await fetch('/api/v1/notifiers');
        if (!response.ok) {
            throw new Error(`Failed to load notifiers: ${response.statusText}`);
        }

        allNotifiers = await response.json();

        loadingEl.style.display = 'none';
        contentEl.style.display = 'block';

        renderNotifiers();
    } catch (error) {
        console.error('Error loading notifiers:', error);
        loadingEl.style.display = 'none';
        showAlert('Failed to load notifiers: ' + error.message, 'danger');
    }
}

/**
 * Render notifiers table
 */
function renderNotifiers() {
    const emptyEl = document.getElementById('notifiers_empty');
    const tableContainer = document.getElementById('notifiers_table_container');
    const tbody = document.getElementById('notifiers_table_body');

    if (allNotifiers.length === 0) {
        emptyEl.style.display = 'block';
        tableContainer.style.display = 'none';
        return;
    }

    emptyEl.style.display = 'none';
    tableContainer.style.display = 'block';

    tbody.innerHTML = allNotifiers.map(notifier => {
        const statusBadge = notifier.enabled
            ? '<span class="badge bg-success"><i class="bi bi-check-circle"></i> Enabled</span>'
            : '<span class="badge bg-secondary"><i class="bi bi-dash-circle"></i> Disabled</span>';

        const typeBadge = notifier.type === 'email'
            ? '<span class="badge bg-primary"><i class="bi bi-envelope"></i> Email</span>'
            : '<span class="badge bg-info"><i class="bi bi-webhook"></i> Webhook</span>';

        const levelBadge = getLevelBadge(notifier.filter_level);

        const createdDate = new Date(notifier.created_at).toLocaleString();

        // Truncate address if too long
        const displayAddress = notifier.address.length > 50
            ? notifier.address.substring(0, 47) + '...'
            : notifier.address;

        return `
            <tr>
                <td>${statusBadge}</td>
                <td><strong>${escapeHtml(notifier.name)}</strong></td>
                <td>${typeBadge}</td>
                <td><code>${escapeHtml(displayAddress)}</code></td>
                <td>${levelBadge}</td>
                <td><small class="text-muted">${createdDate}</small></td>
                <td>
                    <div class="btn-group btn-group-sm" role="group">
                        <button class="btn btn-outline-primary" onclick="testNotifier(${notifier.id})" title="Test">
                            <i class="bi bi-send"></i>
                        </button>
                        <button class="btn btn-outline-secondary" onclick="editNotifier(${notifier.id})" title="Edit">
                            <i class="bi bi-pencil"></i>
                        </button>
                        <button class="btn btn-outline-danger" onclick="deleteNotifier(${notifier.id})" title="Delete">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

/**
 * Get badge HTML for notification level
 */
function getLevelBadge(level) {
    switch(level) {
        case 'info':
            return '<span class="badge bg-info">INFO+</span>';
        case 'warning':
            return '<span class="badge bg-warning">WARNING+</span>';
        case 'error':
            return '<span class="badge bg-danger">ERROR</span>';
        default:
            return '<span class="badge bg-secondary">' + level.toUpperCase() + '</span>';
    }
}

/**
 * Open modal to create a new notifier
 */
function openNotifierModal(notifier = null) {
    currentEditingNotifierId = notifier ? notifier.id : null;

    // Set modal title
    const modalTitle = document.getElementById('notifier_modal_title');
    modalTitle.textContent = notifier ? 'Edit Notifier' : 'Add Notifier';

    // Reset form
    document.getElementById('notifier_form').reset();
    document.getElementById('notifier_id').value = '';

    // If editing, populate form
    if (notifier) {
        document.getElementById('notifier_id').value = notifier.id;
        document.getElementById('notifier_name').value = notifier.name;
        document.getElementById('notifier_type').value = notifier.type;
        document.getElementById('notifier_address').value = notifier.address;
        document.getElementById('notifier_filter_level').value = notifier.filter_level;
        document.getElementById('notifier_enabled').checked = notifier.enabled;

        // Populate SMTP fields if email notifier
        if (notifier.type === 'email') {
            document.getElementById('smtp_host').value = notifier.smtp_host || '';
            document.getElementById('smtp_port').value = notifier.smtp_port || 587;
            document.getElementById('smtp_user').value = notifier.smtp_user || '';
            document.getElementById('smtp_password').value = notifier.smtp_password || '';
            document.getElementById('smtp_sender').value = notifier.smtp_sender || '';
            document.getElementById('smtp_use_tls').checked = notifier.smtp_use_tls !== false;
        }

        // Trigger type change to update placeholder and show/hide SMTP section
        document.getElementById('notifier_type').dispatchEvent(new Event('change'));
    } else {
        // Default values for new notifier
        document.getElementById('notifier_enabled').checked = true;
        document.getElementById('notifier_filter_level').value = 'info';
        document.getElementById('smtp_port').value = 587;
        document.getElementById('smtp_use_tls').checked = true;
    }

    notifierModal.show();
}

/**
 * Edit an existing notifier
 */
function editNotifier(id) {
    const notifier = allNotifiers.find(n => n.id === id);
    if (notifier) {
        openNotifierModal(notifier);
    }
}

/**
 * Save notifier (create or update)
 */
async function saveNotifier() {
    const form = document.getElementById('notifier_form');

    // Validate form
    if (!form.checkValidity()) {
        form.reportValidity();
        return;
    }

    const notifierType = document.getElementById('notifier_type').value;
    const notifierId = document.getElementById('notifier_id').value;
    const data = {
        name: document.getElementById('notifier_name').value,
        type: notifierType,
        address: document.getElementById('notifier_address').value,
        filter_level: document.getElementById('notifier_filter_level').value,
        enabled: document.getElementById('notifier_enabled').checked
    };

    // Add SMTP configuration for email notifiers
    if (notifierType === 'email') {
        const smtpHost = document.getElementById('smtp_host').value;
        const smtpSender = document.getElementById('smtp_sender').value;

        // Validate required SMTP fields
        if (!smtpHost) {
            showAlert('SMTP Host is required for email notifiers', 'danger');
            document.getElementById('smtp_host').focus();
            return;
        }
        if (!smtpSender) {
            showAlert('From Address is required for email notifiers', 'danger');
            document.getElementById('smtp_sender').focus();
            return;
        }

        data.smtp_host = smtpHost;
        data.smtp_port = parseInt(document.getElementById('smtp_port').value) || 587;
        data.smtp_user = document.getElementById('smtp_user').value || null;
        data.smtp_password = document.getElementById('smtp_password').value || null;
        data.smtp_sender = smtpSender;
        data.smtp_use_tls = document.getElementById('smtp_use_tls').checked;
    }

    const saveBtn = document.getElementById('save_notifier_btn');
    const originalBtnText = saveBtn.innerHTML;
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Saving...';

    try {
        let response;
        if (notifierId) {
            // Update existing notifier
            response = await fetch(`/api/v1/notifiers/${notifierId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
        } else {
            // Create new notifier
            response = await fetch('/api/v1/notifiers', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
        }

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to save notifier');
        }

        notifierModal.hide();
        showAlert(
            notifierId ? 'Notifier updated successfully' : 'Notifier created successfully',
            'success'
        );
        await loadNotifiers();
    } catch (error) {
        console.error('Error saving notifier:', error);
        showAlert('Failed to save notifier: ' + error.message, 'danger');
    } finally {
        saveBtn.disabled = false;
        saveBtn.innerHTML = originalBtnText;
    }
}

/**
 * Delete a notifier
 */
function deleteNotifier(id) {
    const notifier = allNotifiers.find(n => n.id === id);
    if (!notifier) return;

    currentEditingNotifierId = id;
    document.getElementById('delete_notifier_name').textContent = notifier.name;
    deleteModal.show();
}

/**
 * Confirm deletion
 */
async function confirmDelete() {
    if (!currentEditingNotifierId) return;

    const deleteBtn = document.getElementById('confirm_delete_btn');
    const originalBtnText = deleteBtn.innerHTML;
    deleteBtn.disabled = true;
    deleteBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Deleting...';

    try {
        const response = await fetch(`/api/v1/notifiers/${currentEditingNotifierId}`, {
            method: 'DELETE'
        });

        if (!response.ok) {
            throw new Error('Failed to delete notifier');
        }

        deleteModal.hide();
        showAlert('Notifier deleted successfully', 'success');
        await loadNotifiers();
    } catch (error) {
        console.error('Error deleting notifier:', error);
        showAlert('Failed to delete notifier: ' + error.message, 'danger');
    } finally {
        deleteBtn.disabled = false;
        deleteBtn.innerHTML = originalBtnText;
        currentEditingNotifierId = null;
    }
}

/**
 * Test a notifier by sending a test notification
 */
async function testNotifier(id) {
    const notifier = allNotifiers.find(n => n.id === id);
    if (!notifier) return;

    showAlert(`Sending test notification to ${notifier.name}...`, 'info');

    try {
        const response = await fetch(`/api/v1/notifiers/${id}/test`, {
            method: 'POST'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Test failed');
        }

        const result = await response.json();

        if (result.success) {
            showAlert(`✓ Test successful! Notification sent to ${notifier.name}`, 'success');
        } else {
            showAlert(`✗ Test failed: ${result.message}`, 'warning');
        }
    } catch (error) {
        console.error('Error testing notifier:', error);
        showAlert('Test failed: ' + error.message, 'danger');
    }
}

/**
 * Show an alert message
 */
function showAlert(message, type = 'info') {
    const alertContainer = document.getElementById('alert_container');
    const alertId = 'alert_' + Date.now();

    const alertHtml = `
        <div id="${alertId}" class="alert alert-${type} alert-dismissible fade show" role="alert">
            ${escapeHtml(message)}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        </div>
    `;

    alertContainer.innerHTML = alertHtml;

    // Auto-dismiss after 5 seconds
    setTimeout(() => {
        const alertEl = document.getElementById(alertId);
        if (alertEl) {
            const bsAlert = new bootstrap.Alert(alertEl);
            bsAlert.close();
        }
    }, 5000);
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
