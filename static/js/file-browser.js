// File Browser JavaScript
// Handles file system browsing with inventory status overlay

class FileBrowser {
    constructor() {
        this.currentPath = '/';
        this.selectedPath = null;
        this.targetInputId = null; // ID of input field to populate when used as picker
        this.onSelectCallback = null; // Callback function when path is selected

        // DOM elements (will be initialized when modal opens)
        this.modal = null;
        this.addressBar = null;
        this.goBtn = null;
        this.refreshBtn = null;
        this.statsEl = null;
        this.loadingEl = null;
        this.errorEl = null;
        this.errorMessageEl = null;
        this.contentEl = null;
        this.tableBody = null;
        this.selectBtn = null;
    }

    /**
     * Initialize the file browser
     */
    init() {
        // Get DOM elements
        this.modal = document.getElementById('fileBrowserModal');
        this.addressBar = document.getElementById('browserAddressBar');
        this.goBtn = document.getElementById('browserGoBtn');
        this.refreshBtn = document.getElementById('browserRefreshBtn');
        this.statsEl = document.getElementById('browserStats');
        this.loadingEl = document.getElementById('browserLoading');
        this.errorEl = document.getElementById('browserError');
        this.errorMessageEl = document.getElementById('browserErrorMessage');
        this.contentEl = document.getElementById('browserContent');
        this.tableBody = document.getElementById('browserTableBody');
        this.selectBtn = document.getElementById('browserSelectBtn');

        // Attach event listeners
        this.attachEventListeners();
    }

    /**
     * Attach event listeners to UI elements
     */
    attachEventListeners() {
        // Go button - navigate to address bar path
        this.goBtn.addEventListener('click', () => {
            const path = this.addressBar.value.trim();
            if (path) {
                this.loadDirectory(path);
            }
        });

        // Enter key on address bar
        this.addressBar.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                const path = this.addressBar.value.trim();
                if (path) {
                    this.loadDirectory(path);
                }
            }
        });

        // Refresh button
        this.refreshBtn.addEventListener('click', () => {
            this.loadDirectory(this.currentPath);
        });

        // Select button (for picker mode)
        this.selectBtn.addEventListener('click', () => {
            this.selectCurrentPath();
        });

        // Modal shown event - load initial directory
        this.modal.addEventListener('shown.bs.modal', () => {
            if (!this.currentPath || this.currentPath === '') {
                this.currentPath = '/';
            }
            this.loadDirectory(this.currentPath);
        });
    }

    /**
     * Open the browser modal
     * @param {Object} options - Configuration options
     * @param {string} options.initialPath - Initial directory to load
     * @param {string} options.targetInputId - ID of input field to populate (picker mode)
     * @param {Function} options.onSelect - Callback when path is selected
     */
    open(options = {}) {
        this.currentPath = options.initialPath || '/';
        this.targetInputId = options.targetInputId || null;
        this.onSelectCallback = options.onSelect || null;

        // Show/hide select button based on picker mode
        if (this.targetInputId || this.onSelectCallback) {
            this.selectBtn.style.display = 'block';
        } else {
            this.selectBtn.style.display = 'none';
        }

        // Open modal
        const modalInstance = new bootstrap.Modal(this.modal);
        modalInstance.show();
    }

    /**
     * Load directory contents from API
     * @param {string} path - Directory path to load
     */
    async loadDirectory(path) {
        this.currentPath = path;
        this.addressBar.value = path;
        this.selectedPath = null; // Clear selection when navigating

        // Show loading state
        this.showLoading();

        try {
            const response = await authenticatedFetch(
                `/api/v1/browser/list?path=${encodeURIComponent(path)}`
            );

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to load directory');
            }

            const data = await response.json();
            this.renderDirectory(data);
        } catch (error) {
            this.showError(error.message);
        }
    }

    /**
     * Render directory contents in the table
     * @param {Object} data - Browser response data
     */
    renderDirectory(data) {
        // Update stats
        this.statsEl.textContent = `${data.total_items} items (${data.total_dirs} folders, ${data.total_files} files)`;

        // Clear table
        this.tableBody.innerHTML = '';

        // Add parent directory link (if not at root)
        if (data.current_path !== '/') {
            const parentPath = data.current_path.split('/').slice(0, -1).join('/') || '/';
            const parentRow = this.createParentRow(parentPath);
            this.tableBody.appendChild(parentRow);
        }

        // Add items
        data.items.forEach(item => {
            const row = this.createItemRow(item);
            this.tableBody.appendChild(row);
        });

        // Show content
        this.hideLoading();
        this.hideError();
        this.contentEl.style.display = 'block';
    }

    /**
     * Create parent directory row (..)
     * @param {string} parentPath - Path to parent directory
     * @returns {HTMLElement} Table row element
     */
    createParentRow(parentPath) {
        const tr = document.createElement('tr');
        tr.className = 'browser-item-row';
        tr.innerHTML = `
            <td><i class="bi bi-folder-fill text-warning"></i></td>
            <td class="browser-dir-name">..</td>
            <td></td>
            <td></td>
            <td></td>
        `;

        tr.addEventListener('click', () => {
            this.loadDirectory(parentPath);
        });

        return tr;
    }

    /**
     * Create item row (file or directory)
     * @param {Object} item - Browser item data
     * @returns {HTMLElement} Table row element
     */
    createItemRow(item) {
        const tr = document.createElement('tr');
        tr.className = 'browser-item-row';

        // Icon
        const icon = item.is_dir
            ? '<i class="bi bi-folder-fill text-warning"></i>'
            : '<i class="bi bi-file-earmark text-secondary"></i>';

        // Name with appropriate styling
        const nameClass = item.is_dir ? 'browser-dir-name' : 'browser-file-name';

        // Size (only for files)
        const size = item.is_dir ? '' : formatBytes(item.size);

        // Inventory status badge
        let statusBadge = '';
        if (item.inventory_status === 'HOT') {
            statusBadge = '<span class="inventory-badge-hot">HOT</span>';
        } else if (item.inventory_status === 'COLD') {
            statusBadge = '<span class="inventory-badge-cold">COLD</span>';
        }

        // Actions
        const selectBtn = item.is_dir
            ? `<button class="btn btn-sm btn-outline-primary" onclick="fileBrowser.selectPath('${escapeHtml(item.path)}'); event.stopPropagation();" title="Select this folder">
                   <i class="bi bi-check-circle"></i>
               </button>`
            : '';

        tr.innerHTML = `
            <td>${icon}</td>
            <td class="${nameClass}">${escapeHtml(item.name)}</td>
            <td class="text-muted small">${size}</td>
            <td>${statusBadge}</td>
            <td class="text-end">${selectBtn}</td>
        `;

        // Click handler - navigate if directory
        if (item.is_dir) {
            tr.addEventListener('click', (e) => {
                // Don't navigate if clicking the select button
                if (!e.target.closest('button')) {
                    this.loadDirectory(item.path);
                }
            });
        }

        return tr;
    }

    /**
     * Select a specific path (for picker mode)
     * @param {string} path - Path to select
     */
    selectPath(path) {
        this.selectedPath = path;
        this.selectCurrentPath();
    }

    /**
     * Select the current directory or selected path
     */
    selectCurrentPath() {
        const pathToSelect = this.selectedPath || this.currentPath;

        // Populate target input if specified
        if (this.targetInputId) {
            const targetInput = document.getElementById(this.targetInputId);
            if (targetInput) {
                targetInput.value = pathToSelect;
            }
        }

        // Call callback if specified
        if (this.onSelectCallback) {
            this.onSelectCallback(pathToSelect);
        }

        // Close modal
        const modalInstance = bootstrap.Modal.getInstance(this.modal);
        if (modalInstance) {
            modalInstance.hide();
        }
    }

    /**
     * Show loading state
     */
    showLoading() {
        this.loadingEl.style.display = 'block';
        this.contentEl.style.display = 'none';
        this.hideError();
    }

    /**
     * Hide loading state
     */
    hideLoading() {
        this.loadingEl.style.display = 'none';
    }

    /**
     * Show error message
     * @param {string} message - Error message to display
     */
    showError(message) {
        this.errorMessageEl.textContent = message;
        this.errorEl.style.display = 'block';
        this.hideLoading();
        this.contentEl.style.display = 'none';
    }

    /**
     * Hide error message
     */
    hideError() {
        this.errorEl.style.display = 'none';
    }
}

// Create global instance
const fileBrowser = new FileBrowser();

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    fileBrowser.init();
});

// Export to window for global access
window.fileBrowser = fileBrowser;
