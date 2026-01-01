// File Fridge - Modern UI JavaScript
// Toast Notification System & Modal Utilities

// ========================================
// Toast Notification System
// ========================================

/**
 * Show a toast notification
 * @param {string} message - The message to display
 * @param {string} type - The toast type: 'success', 'error', 'warning', 'info'
 * @param {number} duration - Auto-dismiss duration in ms (0 = no auto-dismiss)
 */
function showToast(message, type = 'success', duration = 5000) {
    // Get or create toast container
    let container = document.querySelector('.toast-container');
    if (!container) {
        container = document.createElement('div');
        container.className = 'toast-container';
        document.body.appendChild(container);
    }

    // Create toast element
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');
    toast.setAttribute('aria-atomic', 'true');

    // Icon mapping
    const icons = {
        success: 'bi-check-circle-fill',
        error: 'bi-x-circle-fill',
        warning: 'bi-exclamation-triangle-fill',
        info: 'bi-info-circle-fill'
    };

    // Title mapping
    const titles = {
        success: 'Success',
        error: 'Error',
        warning: 'Warning',
        info: 'Information'
    };

    const iconClass = icons[type] || icons.info;
    const title = titles[type] || titles.info;

    toast.innerHTML = `
        <div class="toast-header">
            <i class="bi ${iconClass} me-2"></i>
            <strong class="me-auto">${title}</strong>
            <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
        <div class="toast-body">
            ${escapeHtml(message)}
        </div>
    `;

    // Add to container
    container.appendChild(toast);

    // Initialize Bootstrap toast
    const bsToast = new bootstrap.Toast(toast, {
        autohide: duration > 0,
        delay: duration
    });

    // Show toast
    bsToast.show();

    // Remove from DOM after hiding
    toast.addEventListener('hidden.bs.toast', () => {
        toast.remove();
    });

    return bsToast;
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ========================================
// Confirmation Modal System
// ========================================

/**
 * Show a confirmation modal
 * @param {Object} options - Configuration options
 * @param {string} options.title - Modal title
 * @param {string} options.message - Modal message
 * @param {string} options.confirmText - Text for confirm button (default: 'Confirm')
 * @param {string} options.cancelText - Text for cancel button (default: 'Cancel')
 * @param {string} options.confirmClass - CSS class for confirm button (default: 'btn-primary')
 * @param {boolean} options.dangerous - If true, uses red danger button (default: false)
 * @returns {Promise<boolean>} - Resolves to true if confirmed, false if cancelled
 */
function showConfirmModal(options) {
    return new Promise((resolve) => {
        const {
            title = 'Confirm Action',
            message = 'Are you sure you want to proceed?',
            confirmText = 'Confirm',
            cancelText = 'Cancel',
            confirmClass = 'btn-primary',
            dangerous = false
        } = options;

        // Create modal element
        const modalId = 'confirmModal-' + Date.now();
        const modal = document.createElement('div');
        modal.className = 'modal fade';
        modal.id = modalId;
        modal.setAttribute('tabindex', '-1');
        modal.setAttribute('aria-labelledby', modalId + 'Label');
        modal.setAttribute('aria-hidden', 'true');

        const buttonClass = dangerous ? 'btn-danger' : confirmClass;

        modal.innerHTML = `
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="${modalId}Label">
                            ${dangerous ? '<i class="bi bi-exclamation-triangle-fill text-danger me-2"></i>' : ''}
                            ${escapeHtml(title)}
                        </h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body">
                        ${escapeHtml(message)}
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">${escapeHtml(cancelText)}</button>
                        <button type="button" class="btn ${buttonClass}" id="${modalId}-confirm">${escapeHtml(confirmText)}</button>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        // Initialize Bootstrap modal
        const bsModal = new bootstrap.Modal(modal);

        // Handle confirm button
        const confirmBtn = modal.querySelector(`#${modalId}-confirm`);
        confirmBtn.addEventListener('click', () => {
            bsModal.hide();
            resolve(true);
        });

        // Handle cancel/dismiss
        modal.addEventListener('hidden.bs.modal', () => {
            modal.remove();
            resolve(false);
        });

        // Show modal
        bsModal.show();
    });
}

/**
 * Show a confirmation modal with a checkbox option
 * @param {Object} options - Configuration options
 * @param {string} options.title - Modal title
 * @param {string} options.message - Modal message
 * @param {string} options.checkboxLabel - Label for checkbox
 * @param {boolean} options.checkboxDefault - Default checkbox state (default: false)
 * @param {string} options.confirmText - Text for confirm button (default: 'Confirm')
 * @param {string} options.cancelText - Text for cancel button (default: 'Cancel')
 * @param {boolean} options.dangerous - If true, uses red danger button (default: false)
 * @returns {Promise<{confirmed: boolean, checked: boolean}>}
 */
function showConfirmModalWithCheckbox(options) {
    return new Promise((resolve) => {
        const {
            title = 'Confirm Action',
            message = 'Are you sure you want to proceed?',
            checkboxLabel = 'Additional option',
            checkboxDefault = false,
            confirmText = 'Confirm',
            cancelText = 'Cancel',
            dangerous = false
        } = options;

        const modalId = 'confirmModal-' + Date.now();
        const checkboxId = 'checkbox-' + Date.now();
        const modal = document.createElement('div');
        modal.className = 'modal fade';
        modal.id = modalId;
        modal.setAttribute('tabindex', '-1');

        const buttonClass = dangerous ? 'btn-danger' : 'btn-primary';

        modal.innerHTML = `
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">
                            ${dangerous ? '<i class="bi bi-exclamation-triangle-fill text-danger me-2"></i>' : ''}
                            ${escapeHtml(title)}
                        </h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body">
                        <p>${escapeHtml(message)}</p>
                        <div class="form-check mt-3">
                            <input class="form-check-input" type="checkbox" id="${checkboxId}" ${checkboxDefault ? 'checked' : ''}>
                            <label class="form-check-label" for="${checkboxId}">
                                ${escapeHtml(checkboxLabel)}
                            </label>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">${escapeHtml(cancelText)}</button>
                        <button type="button" class="btn ${buttonClass}" id="${modalId}-confirm">${escapeHtml(confirmText)}</button>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        const bsModal = new bootstrap.Modal(modal);
        const confirmBtn = modal.querySelector(`#${modalId}-confirm`);
        const checkbox = modal.querySelector(`#${checkboxId}`);

        confirmBtn.addEventListener('click', () => {
            bsModal.hide();
            resolve({ confirmed: true, checked: checkbox.checked });
        });

        modal.addEventListener('hidden.bs.modal', () => {
            modal.remove();
            resolve({ confirmed: false, checked: false });
        });

        bsModal.show();
    });
}

// ========================================
// Utility Functions
// ========================================

/**
 * Format bytes to human-readable string
 */
function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

/**
 * Format date to relative time (e.g., "2 hours ago")
 */
function formatRelativeTime(date) {
    const now = new Date();
    const past = new Date(date);
    const seconds = Math.floor((now - past) / 1000);

    if (seconds < 60) return 'just now';
    if (seconds < 3600) return Math.floor(seconds / 60) + ' minutes ago';
    if (seconds < 86400) return Math.floor(seconds / 3600) + ' hours ago';
    if (seconds < 2592000) return Math.floor(seconds / 86400) + ' days ago';
    if (seconds < 31536000) return Math.floor(seconds / 2592000) + ' months ago';
    return Math.floor(seconds / 31536000) + ' years ago';
}

/**
 * Debounce function to limit how often a function is called
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// ========================================
// Global Event Handlers
// ========================================

document.addEventListener('DOMContentLoaded', function() {
    // Legacy: Auto-dismiss old-style alerts (for backward compatibility)
    const alerts = document.querySelectorAll('.alert:not(.toast)');
    alerts.forEach(function(alert) {
        setTimeout(function() {
            const bsAlert = bootstrap.Alert.getInstance(alert);
            if (bsAlert) {
                bsAlert.close();
            }
        }, 5000);
    });

    // Legacy: Confirm delete actions (for forms with confirm attribute)
    const deleteForms = document.querySelectorAll('form[data-confirm]');
    deleteForms.forEach(function(form) {
        form.addEventListener('submit', async function(e) {
            e.preventDefault();
            const message = form.getAttribute('data-confirm') || 'Are you sure you want to delete this item?';
            const confirmed = await showConfirmModal({
                title: 'Confirm Deletion',
                message: message,
                confirmText: 'Delete',
                dangerous: true
            });
            if (confirmed) {
                form.submit();
            }
        });
    });
});

// ========================================
// Export functions to global scope
// ========================================
window.showToast = showToast;
window.showConfirmModal = showConfirmModal;
window.showConfirmModalWithCheckbox = showConfirmModalWithCheckbox;
window.formatBytes = formatBytes;
window.formatRelativeTime = formatRelativeTime;
window.debounce = debounce;
window.escapeHtml = escapeHtml;
