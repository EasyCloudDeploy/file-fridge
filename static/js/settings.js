// Settings page JavaScript

document.addEventListener('DOMContentLoaded', function () {
    // Tab switching
    const navLinks = document.querySelectorAll('#settings-nav .list-group-item');
    const sections = document.querySelectorAll('.settings-section');

    navLinks.forEach(link => {
        link.addEventListener('click', function (e) {
            e.preventDefault();
            const sectionId = this.dataset.section;

            // Update nav
            navLinks.forEach(l => l.classList.remove('active'));
            this.classList.add('active');

            // Update sections
            sections.forEach(s => s.classList.add('d-none'));
            const targetSection = document.getElementById(`${sectionId}-section`);
            if (targetSection) {
                targetSection.classList.remove('d-none');
            }

            // Initial load for remote connections if clicked
            if (sectionId === 'remote-connections') {
                checkRemoteConfiguration();
            }

            // Initial load for encryption if clicked
            if (sectionId === 'encryption') {
                loadEncryptionKeys();
            }

            // Update URL hash without jumping
            history.pushState(null, null, '#' + sectionId);
        });
    });

    // Handle initial hash in URL
    const hash = globalThis.location.hash.substring(1);
    if (hash) {
        const activeLink = document.querySelector(`#settings-nav [data-section="${hash}"]`);
        if (activeLink) {
            activeLink.click();
        }
    }

    // Toggle password visibility
    const togglePasswordBtn = document.getElementById('toggle-password');
    const newPasswordInput = document.getElementById('new-password');
    const toggleIcon = document.getElementById('toggle-icon');

    if (togglePasswordBtn) {
        togglePasswordBtn.addEventListener('click', function () {
            if (newPasswordInput.type === 'password') {
                newPasswordInput.type = 'text';
                toggleIcon.classList.remove('bi-eye');
                toggleIcon.classList.add('bi-eye-slash');
            } else {
                newPasswordInput.type = 'password';
                toggleIcon.classList.remove('bi-eye-slash');
                toggleIcon.classList.add('bi-eye');
            }
        });
    }

    // Password strength indicator
    const newPasswordInputWithStrength = document.getElementById('new-password');
    const passwordStrengthDiv = document.getElementById('password-strength');
    const passwordStrengthText = document.getElementById('password-strength-text');

    if (newPasswordInputWithStrength) {
        newPasswordInputWithStrength.addEventListener('input', function () {
            const password = this.value;
            const strength = calculatePasswordStrength(password);

            if (password.length === 0) {
                passwordStrengthDiv.classList.add('d-none');
            } else {
                passwordStrengthDiv.classList.remove('d-none');
                passwordStrengthDiv.className = 'alert alert-' + getStrengthClass(strength);
                passwordStrengthText.textContent = getStrengthMessage(strength);
            }
        });
    }

    // --- API Token Generation ---

    const generateTokenForm = document.getElementById('generate-token-form');
    if (generateTokenForm) {
        generateTokenForm.addEventListener('submit', async function (e) {
            e.preventDefault();

            const expirySelect = document.getElementById('token-expiry');
            const expiryValue = expirySelect.value;

            // Hide previous messages
            document.getElementById('token-result')?.classList.add('d-none');
            document.getElementById('token-error')?.classList.add('d-none');

            // Disable button during request
            const generateBtn = document.getElementById('generate-token-btn');
            generateBtn.disabled = true;
            const originalText = generateBtn.innerHTML;
            generateBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Generating...';

            try {
                const payload = expiryValue === '' ? {} : { expires_days: parseInt(expiryValue) };

                const response = await authenticatedFetch('/api/v1/auth/tokens', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                if (response.ok) {
                    const data = await response.json();
                    const tokenInput = document.getElementById('generated-token');
                    const tokenResult = document.getElementById('token-result');

                    tokenInput.value = data.access_token;
                    tokenResult.classList.remove('d-none');
                } else {
                    const errorData = await response.json();
                    const tokenError = document.getElementById('token-error');
                    const tokenErrorText = document.getElementById('token-error-text');

                    if (tokenError && tokenErrorText) {
                        tokenErrorText.textContent = errorData.detail || 'Failed to generate token';
                        tokenError.classList.remove('d-none');
                    }
                }
            } catch (error) {
                console.error('Token generation error:', error);
                const tokenError = document.getElementById('token-error');
                const tokenErrorText = document.getElementById('token-error-text');

                if (tokenError && tokenErrorText) {
                    tokenErrorText.textContent = 'Failed to connect to server';
                    tokenError.classList.remove('d-none');
                }
            } finally {
                generateBtn.disabled = false;
                generateBtn.innerHTML = originalText;
            }
        });
    }

    // Copy token button
    const copyTokenBtn = document.getElementById('copy-token-btn');
    if (copyTokenBtn) {
        copyTokenBtn.addEventListener('click', async function () {
            const tokenInput = document.getElementById('generated-token');
            try {
                await navigator.clipboard.writeText(tokenInput.value);

                const originalIcon = this.innerHTML;
                this.innerHTML = '<i class="bi bi-check"></i> Copied!';
                setTimeout(() => {
                    this.innerHTML = originalIcon;
                }, 2000);
            } catch (err) {
                console.error('Failed to copy:', err);
                tokenInput.select();
            }
        });
    }

    // Password change form submission
    const changePasswordForm = document.getElementById('change-password-form');

    if (changePasswordForm) {
        changePasswordForm.addEventListener('submit', async function (e) {
            e.preventDefault();

            const oldPassword = document.getElementById('old-password').value;
            const newPassword = document.getElementById('new-password').value;
            const confirmPassword = document.getElementById('confirm-password').value;

            // Client-side validation
            if (newPassword.length < 8) {
                showError('New password must be at least 8 characters long');
                return;
            }

            if (newPassword !== confirmPassword) {
                showError('New password and confirmation do not match');
                return;
            }

            if (oldPassword === newPassword) {
                showError('New password must be different from current password');
                return;
            }

            hideError();
            hideSuccess();
            setFormButtonLoading('submit', true);

            try {
                // Submit password change request
                const response = await authenticatedFetch('/api/v1/auth/change-password', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        old_password: oldPassword,
                        new_password: newPassword
                    })
                });

                if (response.ok) {
                    const data = await response.json();
                    showSuccess('Password changed successfully! Please login again.');
                    changePasswordForm.reset();
                    setTimeout(() => {
                        handleLogout();
                    }, 2000);
                } else {
                    const data = await response.json();
                    showError(data.detail || 'Failed to change password. Please try again.');
                    setFormButtonLoading('submit', false);
                }
            } catch (error) {
                console.error('Password change error:', error);
                showError('Failed to connect to server. Please try again.');
                setFormButtonLoading('submit', false);
            }
        });
    }

    // --- Remote Connections ---

    // Load instance configuration
    async function loadInstanceConfig() {
        try {
            const response = await authenticatedFetch('/api/v1/remote/config');
            if (response.ok) {
                const config = await response.json();

                // Instance URL
                const urlInput = document.getElementById('instance-url-input');
                const urlSource = document.getElementById('instance-url-source');
                if (urlInput && config.instance_url) {
                    urlInput.value = config.instance_url.value || '';
                    urlInput.disabled = !config.instance_url.can_edit;

                    if (config.instance_url.source === 'environment') {
                        urlSource.innerHTML = '<i class="bi bi-lock-fill me-1"></i>Set via FF_INSTANCE_URL environment variable (read-only)';
                        urlSource.classList.add('text-info');
                    } else if (config.instance_url.source === 'database') {
                        urlSource.innerHTML = '<i class="bi bi-database me-1"></i>Configured in database (can be changed)';
                        urlSource.classList.add('text-success');
                    } else {
                        urlSource.innerHTML = '<i class="bi bi-exclamation-circle me-1"></i>Not configured';
                        urlSource.classList.add('text-warning');
                    }
                }

                // Instance Name
                const nameInput = document.getElementById('instance-name-input');
                const nameSource = document.getElementById('instance-name-source');
                if (nameInput && config.instance_name) {
                    nameInput.value = config.instance_name.value || '';
                    nameInput.disabled = !config.instance_name.can_edit;

                    if (config.instance_name.source === 'environment') {
                        nameSource.innerHTML = '<i class="bi bi-lock-fill me-1"></i>Set via INSTANCE_NAME environment variable (read-only)';
                        nameSource.classList.add('text-info');
                    } else if (config.instance_name.source === 'database') {
                        nameSource.innerHTML = '<i class="bi bi-database me-1"></i>Configured in database (can be changed)';
                        nameSource.classList.add('text-success');
                    }
                }

                // Disable save button if all fields are read-only
                const saveBtn = document.getElementById('save-config-btn');
                if (saveBtn && !config.instance_url.can_edit && !config.instance_name.can_edit) {
                    saveBtn.disabled = true;
                    saveBtn.title = 'Configuration is set via environment variables and cannot be changed';
                }
            }
        } catch (error) {
            console.error('Error loading instance config:', error);
        }
    }

    // Save instance configuration
    const instanceConfigForm = document.getElementById('instance-config-form');
    if (instanceConfigForm) {
        instanceConfigForm.addEventListener('submit', async function (e) {
            e.preventDefault();

            const formData = new FormData(this);
            const data = Object.fromEntries(formData.entries());

            // Hide messages
            document.getElementById('config-error')?.classList.add('d-none');
            document.getElementById('config-success')?.classList.add('d-none');
            setFormButtonLoading('save-config', true);

            try {
                const response = await authenticatedFetch('/api/v1/remote/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });

                if (response.ok) {
                    const configSuccess = document.getElementById('config-success');
                    const configSuccessText = document.getElementById('config-success-text');
                    if (configSuccess && configSuccessText) {
                        configSuccessText.textContent = 'Configuration saved successfully!';
                        configSuccess.classList.remove('d-none');
                    }
                    // Reload config to show updated source info
                    await loadInstanceConfig();
                    // Recheck remote configuration status
                    await checkRemoteConfiguration();
                } else {
                    const errorData = await response.json();
                    const configError = document.getElementById('config-error');
                    const configErrorText = document.getElementById('config-error-text');
                    if (configError && configErrorText) {
                        configErrorText.textContent = errorData.detail || 'Failed to save configuration';
                        configError.classList.remove('d-none');
                    }
                }
            } catch (error) {
                console.error('Error saving config:', error);
                const configError = document.getElementById('config-error');
                const configErrorText = document.getElementById('config-error-text');
                if (configError && configErrorText) {
                    configErrorText.textContent = 'Failed to connect to server.';
                    configError.classList.remove('d-none');
                }
            } finally {
                setFormButtonLoading('save-config', false);
            }
        });
    }

    // Check if remote connections are configured
    async function checkRemoteConfiguration() {
        // Load configuration first
        await loadInstanceConfig();

        try {
            const response = await authenticatedFetch('/api/v1/remote/status');
            if (response.ok) {
                const data = await response.json();
                const warningDiv = document.getElementById('remote-config-warning');
                const remoteConnectionsCard = document.getElementById('remote-connections');
                const remoteTransfersCard = document.getElementById('remote-transfers');
                const addRemoteBtn = document.getElementById('add-remote-btn');
                const connectionCodeInput = document.getElementById('my-connection-code');
                const copyCodeBtn = document.getElementById('copy-code-btn');
                const refreshCodeBtn = document.getElementById('refresh-code-btn');

                if (!data.configured) {
                    // Show warning and disable UI
                    warningDiv.classList.remove('d-none');

                    // Disable buttons
                    if (addRemoteBtn) addRemoteBtn.disabled = true;
                    if (copyCodeBtn) copyCodeBtn.disabled = true;
                    if (refreshCodeBtn) refreshCodeBtn.disabled = true;

                    // Show disabled message in connection code input
                    if (connectionCodeInput) {
                        connectionCodeInput.value = 'Configuration required - see warning above';
                        connectionCodeInput.disabled = true;
                    }

                    // Show configuration message in connections list
                    const list = document.getElementById('remote-connections-list');
                    if (list) {
                        list.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-4">Remote connections are disabled until instance URL is configured above.</td></tr>';
                    }

                    // Show configuration message in transfers list
                    const transfersList = document.getElementById('remote-transfers-list');
                    if (transfersList) {
                        transfersList.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3">Remote connections are disabled until instance URL is configured above.</td></tr>';
                    }
                } else {
                    // Hide warning and enable UI
                    warningDiv.classList.add('d-none');

                    // Enable buttons
                    if (addRemoteBtn) addRemoteBtn.disabled = false;
                    if (copyCodeBtn) copyCodeBtn.disabled = false;
                    if (refreshCodeBtn) refreshCodeBtn.disabled = false;
                    if (connectionCodeInput) connectionCodeInput.disabled = false;

                    // Load data
                    loadRemoteConnections();
                    fetchConnectionCode();
                    loadRemoteTransfers();
                }
            }
        } catch (error) {
            console.error('Error checking remote configuration:', error);
        }
    }

    // Fetch and display connection code
    async function fetchConnectionCode() {
        try {
            const response = await authenticatedFetch('/api/v1/remote/connection-code');
            if (response.ok) {
                const data = await response.json();
                document.getElementById('my-connection-code').value = data.code;
            } else if (response.status === 500) {
                // Configuration error - likely FF_INSTANCE_URL not set
                const codeInput = document.getElementById('my-connection-code');
                if (codeInput) {
                    codeInput.value = 'Configuration required';
                    codeInput.disabled = true;
                }
            }
        } catch (error) {
            console.error('Error fetching connection code:', error);
        }
    }

    const toggleCodeVisibilityBtn = document.getElementById('toggle-code-visibility');
    if (toggleCodeVisibilityBtn) {
        toggleCodeVisibilityBtn.addEventListener('click', function () {
            const codeInput = document.getElementById('my-connection-code');
            const icon = this.querySelector('i');
            if (codeInput.type === 'password') {
                codeInput.type = 'text';
                icon.classList.replace('bi-eye', 'bi-eye-slash');
            } else {
                codeInput.type = 'password';
                icon.classList.replace('bi-eye-slash', 'bi-eye');
            }
        });
    }

    const refreshCodeBtn = document.getElementById('refresh-code-btn');
    if (refreshCodeBtn) {
        refreshCodeBtn.addEventListener('click', fetchConnectionCode);
    }

    const copyCodeBtn = document.getElementById('copy-code-btn');
    if (copyCodeBtn) {
        copyCodeBtn.addEventListener('click', async function () {
            const codeInput = document.getElementById('my-connection-code');
            try {
                await navigator.clipboard.writeText(codeInput.value);

                const originalIcon = this.innerHTML;
                this.innerHTML = '<i class="bi bi-check"></i>';
                setTimeout(() => {
                    this.innerHTML = originalIcon;
                }, 2000);
            } catch (err) {
                console.error('Failed to copy:', err);
                // Fallback: select text for manual copy
                codeInput.select();
            }
        });
    }

    // Load remote connections
    async function loadRemoteConnections() {
        const list = document.getElementById('remote-connections-list');
        if (!list) return;

        try {
            const response = await authenticatedFetch('/api/v1/remote/connections');
            if (response.ok) {
                const connections = await response.json();
                list.innerHTML = '';

                if (connections.length === 0) {
                    list.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-4">No remote connections found.</td></tr>';
                    return;
                }

                connections.forEach(conn => {
                    const date = new Date(conn.created_at).toLocaleString();
                    const tr = document.createElement('tr');
                    // Escape HTML for display
                    const escapedName = escapeHtml(conn.name);
                    const escapedUrl = escapeHtml(conn.url);
                    // Escape quotes for data attributes (use original values, not HTML-escaped)
                    const safeName = conn.name.replace(/"/g, '&quot;');
                    const safeUrl = conn.url.replace(/"/g, '&quot;');
                    const safeMode = (conn.transfer_mode || 'PUSH_ONLY').replace(/"/g, '&quot;');

                    // Build trust status badge
                    const trustStatus = conn.trust_status || 'PENDING';
                    let statusBadge = '';
                    if (trustStatus === 'TRUSTED') {
                        statusBadge = '<span class="badge bg-success">Trusted</span>';
                    } else if (trustStatus === 'PENDING') {
                        statusBadge = '<span class="badge bg-warning">Pending</span>';
                    } else if (trustStatus === 'REJECTED') {
                        statusBadge = '<span class="badge bg-danger">Rejected</span>';
                    }

                    // Build mode display
                    const modeLabel = conn.transfer_mode === 'BIDIRECTIONAL' ? 'Bidirectional' : 'Push Only';
                    let modeBadge = '';
                    if (conn.effective_bidirectional) {
                        modeBadge = '<span class="badge bg-success ms-1">Active</span>';
                    } else if (conn.transfer_mode === 'BIDIRECTIONAL') {
                        modeBadge = '<span class="badge bg-warning ms-1">Pending Remote</span>';
                    }

                    // Browse button - disabled for non-trusted connections
                    const browseBtn = (trustStatus === 'TRUSTED' && conn.effective_bidirectional)
                        ? `<button class="btn btn-sm btn-primary browse-remote-btn w-100" data-id="${conn.id}" title="Browse remote files">
                             <i class="bi bi-folder2-open me-1"></i> Browse Files
                           </button>`
                        : `<button class="btn btn-sm btn-outline-secondary w-100" disabled title="${trustStatus === 'PENDING' ? 'Accept connection first' : 'Enable Bidirectional mode on both instances'}">
                             <i class="bi bi-lock me-1"></i> Locked
                           </button>`;

                    // Action buttons - different for pending vs trusted connections
                    let actionButtons = '';
                    if (trustStatus === 'PENDING') {
                        const safeFingerprint = (conn.remote_fingerprint || '').replace(/"/g, '&quot;');
                        actionButtons = `
                            <button class="btn btn-sm btn-outline-info view-details-btn me-1"
                                data-id="${conn.id}"
                                data-name="${safeName}"
                                data-url="${safeUrl}"
                                data-fingerprint="${safeFingerprint}"
                                data-mode="${safeMode}"
                                data-status="${trustStatus}"
                                title="View connection details">
                                <i class="bi bi-info-circle"></i>
                            </button>
                            <button class="btn btn-sm btn-success accept-conn-btn me-1" data-id="${conn.id}" data-name="${safeName}" title="Accept connection">
                                <i class="bi bi-check-circle"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-danger reject-conn-btn" data-id="${conn.id}" data-name="${safeName}" title="Reject connection">
                                <i class="bi bi-x-circle"></i>
                            </button>
                        `;
                    } else if (trustStatus === 'TRUSTED') {
                        actionButtons = `
                            <button class="btn btn-sm btn-outline-primary edit-conn-btn me-1" data-id="${conn.id}" data-name="${safeName}" data-url="${safeUrl}" data-mode="${safeMode}" title="Edit connection">
                                <i class="bi bi-pencil"></i>
                            </button>
                            <button class="btn btn-sm btn-outline-danger delete-conn-btn" data-id="${conn.id}" data-name="${safeName}" title="Delete connection">
                                <i class="bi bi-trash"></i>
                            </button>
                        `;
                    } else {
                        // REJECTED - only show delete
                        actionButtons = `
                            <button class="btn btn-sm btn-outline-danger delete-conn-btn" data-id="${conn.id}" data-name="${safeName}" title="Delete connection">
                                <i class="bi bi-trash"></i>
                            </button>
                        `;
                    }

                    tr.innerHTML = `
                        <td>${escapedName}</td>
                        <td>${escapedUrl}</td>
                        <td>${statusBadge}</td>
                        <td>${modeLabel}${modeBadge}</td>
                        <td>${date}</td>
                        <td>${browseBtn}</td>
                        <td>${actionButtons}</td>
                    `;
                    list.appendChild(tr);
                });

                // Add view details event listeners
                document.querySelectorAll('.view-details-btn').forEach(btn => {
                    btn.addEventListener('click', function () {
                        const id = this.dataset.id;
                        const name = this.dataset.name;
                        const url = this.dataset.url;
                        const fingerprint = this.dataset.fingerprint;
                        const mode = this.dataset.mode;
                        const status = this.dataset.status;
                        showConnectionDetails(id, name, url, fingerprint, mode, status);
                    });
                });

                // Add accept event listeners
                document.querySelectorAll('.accept-conn-btn').forEach(btn => {
                    btn.addEventListener('click', function () {
                        const id = this.dataset.id;
                        const name = this.dataset.name;
                        acceptConnection(id, name);
                    });
                });

                // Add reject event listeners
                document.querySelectorAll('.reject-conn-btn').forEach(btn => {
                    btn.addEventListener('click', function () {
                        const id = this.dataset.id;
                        const name = this.dataset.name;
                        rejectConnection(id, name);
                    });
                });

                // Add edit event listeners
                document.querySelectorAll('.edit-conn-btn').forEach(btn => {
                    btn.addEventListener('click', function () {
                        const id = this.dataset.id;
                        const name = this.dataset.name;
                        const url = this.dataset.url;
                        const mode = this.dataset.mode;
                        showEditModal(id, name, url, mode);
                    });
                });

                // Add delete event listeners
                document.querySelectorAll('.delete-conn-btn').forEach(btn => {
                    btn.addEventListener('click', function () {
                        const id = this.dataset.id;
                        const name = this.dataset.name;
                        showDeleteModal(id, name);
                    });
                });

                // Add browse event listeners
                document.querySelectorAll('.browse-remote-btn').forEach(btn => {
                    btn.addEventListener('click', function () {
                        const id = this.dataset.id;
                        window.location.href = `/remote-files/${id}`;
                    });
                });
            }
        } catch (error) {
            console.error('Error loading connections:', error);
            list.innerHTML = '<tr><td colspan="7" class="text-center text-danger py-4">Failed to load connections.</td></tr>';
        }
    }

    // Track if we're editing or adding
    let editingConnectionId = null;

    // Show edit modal
    function showEditModal(id, name, url, transferMode) {
        editingConnectionId = id;
        const modal = document.getElementById('addConnectionModal');
        const modalTitle = document.getElementById('addConnectionModalLabel');
        const nameInput = document.getElementById('remote-name');
        const urlInput = document.getElementById('remote-url');
        const codeInput = document.getElementById('connection-code');
        const codeGroup = codeInput.closest('.mb-3');
        const saveBtn = document.getElementById('save-connection-text');
        const modeSelect = document.getElementById('transfer-mode');

        // Update modal title
        modalTitle.textContent = 'Edit Remote Connection';

        // Populate form fields
        nameInput.value = name;
        urlInput.value = url;
        if (modeSelect) modeSelect.value = transferMode || 'PUSH_ONLY';

        // Hide connection code field for editing
        codeGroup.classList.add('d-none');
        codeInput.removeAttribute('required');

        // Update button text
        saveBtn.textContent = 'Update Connection';

        // Show modal
        const modalInstance = new bootstrap.Modal(modal);
        modalInstance.show();
    }

    // Reset modal to add mode
    function resetModalToAddMode() {
        editingConnectionId = null;
        const modalTitle = document.getElementById('addConnectionModalLabel');
        const codeInput = document.getElementById('connection-code');
        const codeGroup = codeInput.closest('.mb-3');
        const saveBtn = document.getElementById('save-connection-text');

        modalTitle.textContent = 'Add Remote Connection';
        codeGroup.classList.remove('d-none');
        codeInput.setAttribute('required', 'required');
        saveBtn.textContent = 'Add Connection';
    }

    // Add connection form
    const addConnectionForm = document.getElementById('add-connection-form');
    if (addConnectionForm) {
        // Reset modal when it's hidden
        const addConnectionModal = document.getElementById('addConnectionModal');
        if (addConnectionModal) {
            addConnectionModal.addEventListener('hidden.bs.modal', function () {
                addConnectionForm.reset();
                resetModalToAddMode();
            });
        }

        addConnectionForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            setFormButtonLoading('save-connection', true);

            const formData = new FormData(this);
            const data = Object.fromEntries(formData.entries());

            try {
                let response;
                if (editingConnectionId) {
                    // Update existing connection
                    const updateData = {
                        name: data.name,
                        url: data.url,
                        transfer_mode: data.transfer_mode || 'PUSH_ONLY',
                    };
                    response = await authenticatedFetch(`/api/v1/remote/connections/${editingConnectionId}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(updateData)
                    });
                } else {
                    // Create new connection
                    response = await authenticatedFetch('/api/v1/remote/connect', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data)
                    });
                }

                if (response.ok) {
                    bootstrap.Modal.getInstance(document.getElementById('addConnectionModal')).hide();
                    addConnectionForm.reset();
                    resetModalToAddMode();
                    loadRemoteConnections();
                } else {
                    const errorData = await response.json();
                    alert('Error: ' + (errorData.detail || 'Failed to save connection'));
                }
            } catch (error) {
                console.error('Error saving connection:', error);
                alert('Failed to connect to server.');
            } finally {
                setFormButtonLoading('save-connection', false);
            }
        });
    }

    // Connection details modal
    let currentDetailConnectionId = null;
    let currentDetailConnectionName = null;
    const detailsModal = new bootstrap.Modal(document.getElementById('connectionDetailsModal'));

    function showConnectionDetails(id, name, url, fingerprint, mode, status) {
        currentDetailConnectionId = id;
        currentDetailConnectionName = name;

        document.getElementById('detail-name').textContent = name;
        document.getElementById('detail-url').textContent = url;
        document.getElementById('detail-fingerprint').textContent = fingerprint || 'N/A';
        document.getElementById('detail-status').textContent = status;
        document.getElementById('detail-mode').textContent = mode === 'BIDIRECTIONAL' ? 'Bidirectional' : 'Push Only';

        // Show/hide accept button based on status
        const acceptBtn = document.getElementById('accept-from-details-btn');
        if (status === 'PENDING') {
            acceptBtn.style.display = '';
        } else {
            acceptBtn.style.display = 'none';
        }

        detailsModal.show();
    }

    // Accept from details modal
    document.getElementById('accept-from-details-btn').addEventListener('click', async function () {
        if (currentDetailConnectionId) {
            detailsModal.hide();
            await acceptConnection(currentDetailConnectionId, currentDetailConnectionName);
        }
    });

    // Accept connection logic
    async function acceptConnection(id, name) {
        if (!confirm(`Accept connection from "${name}"?`)) {
            return;
        }

        try {
            const response = await authenticatedFetch(`/api/v1/remote/connections/${id}/trust`, {
                method: 'POST'
            });

            if (response.ok) {
                showToast('Success', `Connection "${name}" has been accepted.`, 'success');
                loadRemoteConnections();
            } else {
                const errorData = await response.json();
                showToast('Error', errorData.detail || 'Failed to accept connection', 'danger');
            }
        } catch (error) {
            console.error('Error accepting connection:', error);
            showToast('Error', 'Failed to connect to server.', 'danger');
        }
    }

    // Reject connection logic
    async function rejectConnection(id, name) {
        if (!confirm(`Reject connection from "${name}"? This can be reversed later.`)) {
            return;
        }

        try {
            const response = await authenticatedFetch(`/api/v1/remote/connections/${id}/reject`, {
                method: 'POST'
            });

            if (response.ok) {
                showToast('Success', `Connection "${name}" has been rejected.`, 'warning');
                loadRemoteConnections();
            } else {
                const errorData = await response.json();
                showToast('Error', errorData.detail || 'Failed to reject connection', 'danger');
            }
        } catch (error) {
            console.error('Error rejecting connection:', error);
            showToast('Error', 'Failed to connect to server.', 'danger');
        }
    }

    // Delete connection logic
    let connectionToDelete = null;
    const deleteModal = new bootstrap.Modal(document.getElementById('deleteConnectionModal'));

    function showDeleteModal(id, name) {
        connectionToDelete = id;
        document.getElementById('delete-conn-name').textContent = name;
        document.getElementById('force-delete-check').checked = false;
        document.getElementById('force-delete-warning').classList.add('d-none');
        deleteModal.show();
    }

    document.getElementById('force-delete-check').addEventListener('change', function () {
        const warning = document.getElementById('force-delete-warning');
        if (this.checked) {
            warning.classList.remove('d-none');
        } else {
            warning.classList.add('d-none');
        }
    });

    // Delete transfer logic
    let transferToDelete = null;
    let transferFileName = '';
    const deleteTransferModal = new bootstrap.Modal(document.getElementById('deleteTransferModal'));

    function showDeleteTransferModal(jobId, fileName) {
        transferToDelete = jobId;
        transferFileName = fileName;
        document.getElementById('delete-transfer-name').textContent = fileName;
        deleteTransferModal.show();
    }

    // Load remote transfers
    let transfers = [];
    async function loadRemoteTransfers() {
        const list = document.getElementById('remote-transfers-list');
        try {
            const response = await authenticatedFetch('/api/v1/remote/transfers');
            if (response.ok) {
                transfers = await response.json();
                if (transfers.length === 0) {
                    list.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3">No active transfers.</td></tr>';
                    return;
                }

                list.innerHTML = '';
                transfers.forEach(job => {
                    const fileName = job.source_path.split('/').pop();
                    let statusClass = 'secondary';
                    if (job.status === 'completed') {
                        statusClass = 'success';
                    } else if (job.status === 'failed') {
                        statusClass = 'danger';
                    } else if (job.status === 'in_progress') {
                        statusClass = 'primary';
                    } else if (job.status === 'cancelled') {
                        statusClass = 'warning';
                    }

                    const directionBadge = job.direction === 'PULL'
                        ? '<span class="badge bg-info me-1" title="Pull transfer (serving to remote)"><i class="bi bi-arrow-up"></i></span>'
                        : '<span class="badge bg-secondary me-1" title="Push transfer"><i class="bi bi-arrow-right"></i></span>';

                    const canCancel = ['pending', 'in_progress'].includes(job.status);
                    const canDelete = ['failed', 'completed', 'cancelled'].includes(job.status);
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td title="${job.source_path}">${directionBadge}${fileName}</td>
                        <td><span class="badge bg-${statusClass}">${job.status}</span></td>
                        <td>
                            <div class="progress" style="height: 10px; width: 100px;">
                                <div class="progress-bar" role="progressbar" style="width: ${job.progress}%"></div>
                            </div>
                            <small>${job.progress}%</small>
                        </td>
                        <td>${formatETA(job.eta)}</td>
                        <td>
                            ${job.error_message ? `<i class="bi bi-exclamation-circle text-danger" title="${job.error_message}"></i>` : ''}
                            ${canCancel ? `<button class="btn btn-sm btn-outline-danger ms-2" onclick="cancelTransfer(${job.id})" title="Cancel transfer"><i class="bi bi-x-circle"></i></button>` : ''}
                            ${canDelete ? `<button class="btn btn-sm btn-outline-secondary ms-2" onclick="showDeleteTransferModal(${job.id}, '${fileName.replace(/'/g, "\\\'")}')" title="Remove from list"><i class="bi bi-trash"></i></button>` : ''}
                        </td>
                    `;
                    list.appendChild(tr);
                });
            }
        } catch (error) {
            console.error('Error loading transfers:', error);
        }
    }

    function formatETA(seconds) {
        if (!seconds || seconds < 0) return '-';
        if (seconds < 60) return Math.round(seconds) + 's';
        const mins = Math.floor(seconds / 60);
        const secs = Math.round(seconds % 60);
        return mins + 'm ' + secs + 's';
    }

    globalThis.cancelTransfer = async function (jobId) {
        if (!confirm('Are you sure you want to cancel this transfer?')) return;

        try {
            const response = await authenticatedFetch(`/api/v1/remote/transfers/${jobId}/cancel`, {
                method: 'POST'
            });

            if (response.ok) {
                await loadRemoteTransfers();
            } else {
                const errorData = await response.json();
                alert('Error: ' + (errorData.detail || 'Failed to cancel transfer'));
            }
        } catch (error) {
            console.error('Error cancelling transfer:', error);
            alert('Failed to connect to server.');
        }
    };

    // Make showDeleteTransferModal globally accessible for inline onclick
    globalThis.showDeleteTransferModal = showDeleteTransferModal;

    // Confirm delete transfer button handler
    document.getElementById('confirm-delete-transfer-btn').addEventListener('click', async function () {
        if (!transferToDelete) return;

        const button = this;
        setButtonTextLoading(button, true, 'Removing...', 'Remove');

        try {
            const response = await authenticatedFetch(`/api/v1/remote/transfers/${transferToDelete}`, {
                method: 'DELETE'
            });

            if (response.ok) {
                deleteTransferModal.hide();
                await loadRemoteTransfers();
            } else {
                const errorData = await response.json();
                alert('Error: ' + (errorData.detail || 'Failed to delete transfer'));
            }
        } catch (error) {
            console.error('Error deleting transfer:', error);
            alert('Failed to connect to server.');
        } finally {
            setButtonTextLoading(button, false, 'Removing...', 'Remove');
        }
    });

    globalThis.bulkCancelTransfers = async function () {
        const pendingJobs = transfers.filter(t => ['pending', 'in_progress'].includes(t.status));
        if (pendingJobs.length === 0) {
            alert('No pending or in-progress transfers to cancel.');
            return;
        }

        if (!confirm(`Cancel ${pendingJobs.length} transfers?`)) return;

        try {
            const jobIds = pendingJobs.map(t => t.id);
            const response = await authenticatedFetch('/api/v1/remote/transfers/bulk/cancel', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(jobIds)
            });

            if (response.ok) {
                const data = await response.json();
                await loadRemoteTransfers();
                alert(`Cancelled ${data.cancelled_count} transfers. ${data.error_count > 0 ? data.error_count + ' errors occurred.' : ''}`);
            } else {
                const errorData = await response.json();
                alert('Error: ' + (errorData.detail || 'Failed to cancel transfers'));
            }
        } catch (error) {
            console.error('Error cancelling transfers:', error);
            alert('Failed to connect to server.');
        }
    };

    globalThis.bulkRetryTransfers = async function () {
        const failedJobs = transfers.filter(t => t.status === 'failed');
        if (failedJobs.length === 0) {
            alert('No failed transfers to retry.');
            return;
        }

        if (!confirm(`Retry ${failedJobs.length} transfers?`)) return;

        try {
            const jobIds = failedJobs.map(t => t.id);
            const response = await authenticatedFetch('/api/v1/remote/transfers/bulk/retry', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ job_ids: jobIds })
            });

            if (response.ok) {
                const data = await response.json();
                await loadRemoteTransfers();
                alert(`Retrying ${data.retried_count} transfers. ${data.skipped_count > 0 ? data.skipped_count + ' transfers skipped (max retries exceeded).' : ''}`);
            } else {
                const errorData = await response.json();
                alert('Error: ' + (errorData.detail || 'Failed to retry transfers'));
            }
        } catch (error) {
            console.error('Error retrying transfers:', error);
            alert('Failed to connect to server.');
        }
    };

    const refreshTransfersBtn = document.getElementById('refresh-transfers-btn');
    if (refreshTransfersBtn) {
        refreshTransfersBtn.addEventListener('click', loadRemoteTransfers);
    }

    // Auto-refresh transfers if visible (only if configured)
    let remoteConfigured = false;
    setInterval(() => {
        const section = document.getElementById('remote-connections-section');
        const warningDiv = document.getElementById('remote-config-warning');
        // Only refresh if section is visible and configuration warning is hidden
        if (section && !section.classList.contains('d-none') && warningDiv && warningDiv.classList.contains('d-none')) {
            loadRemoteTransfers();
        }
    }, 5000);

    document.getElementById('confirm-delete-conn-btn').addEventListener('click', async function () {
        if (!connectionToDelete) return;

        const button = this;
        setButtonTextLoading(button, true, 'Deleting...', 'Delete');

        try {
            const force = document.getElementById('force-delete-check').checked;
            const response = await authenticatedFetch(`/api/v1/remote/connections/${connectionToDelete}?force=${force}`, {
                method: 'DELETE'
            });

            if (response.ok) {
                deleteModal.hide();
                loadRemoteConnections();
            } else {
                const errorData = await response.json();
                alert('Error: ' + (errorData.detail || 'Failed to delete connection'));
            }
        } catch (error) {
            console.error('Error deleting connection:', error);
            alert('Failed to connect to server.');
        } finally {
            setButtonTextLoading(button, false, 'Deleting...', 'Delete');
        }
    });

    // --- Encryption Management ---

    async function loadEncryptionKeys() {
        const list = document.getElementById('encryption-keys-list');
        if (!list) return;

        try {
            const response = await authenticatedFetch('/api/v1/encryption/keys');
            if (response.ok) {
                const keys = await response.json();
                list.innerHTML = '';

                if (keys.length === 0) {
                    list.innerHTML = '<tr><td colspan="4" class="text-center text-muted py-4">No encryption keys found.</td></tr>';
                    return;
                }

                keys.forEach(key => {
                    const date = new Date(key.created_at).toLocaleString();
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td>${key.id}</td>
                        <td class="font-monospace small text-break">${key.fingerprint}</td>
                        <td>${date}</td>
                        <td class="text-end">
                            <button class="btn btn-sm btn-outline-danger btn-delete-key" data-id="${key.id}">
                                <i class="bi bi-trash"></i>
                            </button>
                        </td>
                    `;
                    list.appendChild(tr);
                });

                // Add delete event listeners
                document.querySelectorAll('.btn-delete-key').forEach(btn => {
                    btn.addEventListener('click', function () {
                        deleteEncryptionKey(this.dataset.id);
                    });
                });
            }
        } catch (error) {
            console.error('Error loading encryption keys:', error);
            list.innerHTML = '<tr><td colspan="4" class="text-center text-danger py-4">Failed to load encryption keys.</td></tr>';
        }
    }

    async function deleteEncryptionKey(keyId) {
        if (!confirm('Are you sure you want to delete this encryption key? This cannot be undone. Any data encrypted EXCLUSIVELY with this key will become unreadable and its password field will be cleared.')) {
            return;
        }

        try {
            const response = await authenticatedFetch(`/api/v1/encryption/keys/${keyId}`, {
                method: 'DELETE'
            });

            if (response.ok) {
                loadEncryptionKeys();
            } else {
                const errorData = await response.json();
                alert('Error: ' + (errorData.detail || 'Failed to delete encryption key'));
            }
        } catch (error) {
            console.error('Error deleting encryption key:', error);
            alert('Failed to connect to server.');
        }
    }

    const btnGenerateKey = document.getElementById('btn-generate-key');
    if (btnGenerateKey) {
        btnGenerateKey.addEventListener('click', async function () {
            if (!confirm('Are you sure you want to generate a new encryption key? This will rotate the current active key. New data will use this key, while existing data remains readable using old keys.')) {
                return;
            }

            this.disabled = true;
            try {
                const response = await authenticatedFetch('/api/v1/encryption/keys', {
                    method: 'POST'
                });

                if (response.ok) {
                    loadEncryptionKeys();
                } else {
                    const errorData = await response.json();
                    alert('Error: ' + (errorData.detail || 'Failed to generate encryption key'));
                }
            } catch (error) {
                console.error('Error generating encryption key:', error);
                alert('Failed to connect to server.');
            } finally {
                this.disabled = false;
            }
        });
    }

    // --- User Management ---

    /**
     * Parse JWT to extract roles
     */
    function parseJwt(token) {
        try {
            const base64Url = token.split('.')[1];
            const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
            const jsonPayload = decodeURIComponent(atob(base64).split('').map(function (c) {
                return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
            }).join(''));
            return JSON.parse(jsonPayload);
        } catch (e) {
            return null;
        }
    }

    const payload = parseJwt(sessionStorage.getItem('auth_token'));

    /**
     * Check if current user is an admin and show admin-only elements
     */
    function initUserManagement() {
        if (!payload) return;

        if (payload.roles && payload.roles.includes('admin')) {
            // Show admin-only elements
            document.querySelectorAll('.admin-only').forEach(el => el.classList.remove('d-none'));

            // If the hash is #users, load the users list
            if (globalThis.location.hash === '#users') {
                loadUsers();
            }
        }
    }

    initUserManagement();

    // Listen for tab switch to users
    navLinks.forEach(link => {
        link.addEventListener('click', function () {
            if (this.dataset.section === 'users') {
                loadUsers();
            }
        });
    });

    async function loadUsers() {
        const list = document.getElementById('users-list');
        if (!list) return;

        try {
            const response = await authenticatedFetch('/api/v1/users');
            if (response.ok) {
                const users = await response.json();
                list.innerHTML = '';

                if (users.length === 0) {
                    list.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">No users found.</td></tr>';
                    return;
                }

                users.forEach(user => {
                    const date = new Date(user.created_at).toLocaleDateString();
                    const tr = document.createElement('tr');

                    // Simple badges for roles
                    const roleBadges = user.roles.map(r =>
                        `<span class="badge bg-secondary me-1">${r}</span>`
                    ).join('');

                    const statusBadge = user.is_active ?
                        '<span class="badge bg-success" title="User is allowed to login">Active</span>' :
                        '<span class="badge bg-danger" title="User is blocked from login">Inactive</span>';

                    tr.innerHTML = `
                        <td><strong>${escapeHtml(user.username)}</strong></td>
                        <td>${roleBadges}</td>
                        <td>${statusBadge}</td>
                        <td>${date}</td>
                        <td class="text-end">
                            <button class="btn btn-sm btn-outline-primary btn-edit-roles me-1" 
                                    data-id="${user.id}" data-username="${escapeHtml(user.username)}" data-roles='${JSON.stringify(user.roles)}' title="Edit Roles">
                                <i class="bi bi-shield-check"></i>
                            </button>
                            ${user.username !== payload.sub ? `
                            <button class="btn btn-sm btn-outline-danger btn-delete-user" data-id="${user.id}" data-username="${escapeHtml(user.username)}" title="Delete User">
                                <i class="bi bi-trash"></i>
                            </button>` : `<small class="text-muted">(You)</small>`}
                        </td>
                    `;
                    list.appendChild(tr);
                });

                // Add event listeners
                document.querySelectorAll('.btn-edit-roles').forEach(btn => {
                    btn.addEventListener('click', function () {
                        showEditRolesModal(this.dataset.id, this.dataset.username, JSON.parse(this.dataset.roles));
                    });
                });

                document.querySelectorAll('.btn-delete-user').forEach(btn => {
                    btn.addEventListener('click', function () {
                        showDeleteUserModal(this.dataset.id, this.dataset.username);
                    });
                });
            }
        } catch (error) {
            console.error('Error loading users:', error);
            list.innerHTML = '<tr><td colspan="5" class="text-center text-danger py-4">Failed to load users.</td></tr>';
        }
    }

    // Modal instances
    const addUserModalElement = document.getElementById('addUserModal');
    const editRolesModalElement = document.getElementById('editRolesModal');
    const deleteUserModalElement = document.getElementById('deleteUserModal');

    let addUserModal, editRolesModal, deleteUserModal;

    if (addUserModalElement) addUserModal = new bootstrap.Modal(addUserModalElement);
    if (editRolesModalElement) editRolesModal = new bootstrap.Modal(editRolesModalElement);
    if (deleteUserModalElement) deleteUserModal = new bootstrap.Modal(deleteUserModalElement);

    // Add user button
    document.getElementById('add-user-btn')?.addEventListener('click', () => {
        document.getElementById('add-user-form').reset();
        addUserModal?.show();
    });

    // Add user form submission
    document.getElementById('add-user-form')?.addEventListener('submit', async function (e) {
        e.preventDefault();
        const formData = new FormData(this);
        const data = Object.fromEntries(formData.entries());

        // Collect selected roles
        const roles = ['viewer']; // default
        if (document.getElementById('role-manager').checked) roles.push('manager');
        if (document.getElementById('role-admin').checked) roles.push('admin');

        setFormButtonLoading('save-user', true);

        try {
            const response = await authenticatedFetch('/api/v1/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });

            if (response.ok) {
                const newUser = await response.json();

                // If roles were specified, update them (user creation API defaults to viewer)
                if (roles.length > 1) {
                    await authenticatedFetch(`/api/v1/users/${newUser.id}/roles`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(roles)
                    });
                }

                addUserModal?.hide();
                showToast(`User ${data.username} created successfully`, 'success');
                loadUsers();
            } else {
                const err = await response.json();
                showToast(err.detail || 'Failed to create user', 'error');
            }
        } catch (error) {
            showToast('Connection error', 'error');
        } finally {
            setFormButtonLoading('save-user', false);
        }
    });

    // Edit roles
    let currentUserIdForRoles = null;
    function showEditRolesModal(id, username, roles) {
        currentUserIdForRoles = id;
        document.getElementById('edit-roles-username').textContent = username;

        // Reset checkboxes
        document.getElementById('edit-role-viewer').checked = roles.includes('viewer');
        document.getElementById('edit-role-manager').checked = roles.includes('manager');
        document.getElementById('edit-role-admin').checked = roles.includes('admin');

        editRolesModal?.show();
    }

    document.getElementById('confirm-roles-btn')?.addEventListener('click', async function () {
        const roles = [];
        if (document.getElementById('edit-role-viewer').checked) roles.push('viewer');
        if (document.getElementById('edit-role-manager').checked) roles.push('manager');
        if (document.getElementById('edit-role-admin').checked) roles.push('admin');

        const btn = this;
        setButtonTextLoading(btn, true, 'Updating...', 'Update Roles');

        try {
            const response = await authenticatedFetch(`/api/v1/users/${currentUserIdForRoles}/roles`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(roles)
            });

            if (response.ok) {
                editRolesModal?.hide();
                showToast('Roles updated successfully', 'success');
                loadUsers();
            } else {
                const err = await response.json();
                showToast(err.detail || 'Failed to update roles', 'error');
            }
        } catch (error) {
            showToast('Connection error', 'error');
        } finally {
            setButtonTextLoading(btn, false, 'Updating...', 'Update Roles');
        }
    });

    // Delete user
    let userToDeleteId = null;
    function showDeleteUserModal(id, username) {
        userToDeleteId = id;
        document.getElementById('delete-user-name').textContent = username;
        deleteUserModal?.show();
    }

    document.getElementById('confirm-delete-user-btn')?.addEventListener('click', async function () {
        const btn = this;
        setButtonTextLoading(btn, true, 'Deleting...', 'Delete User');

        try {
            const response = await authenticatedFetch(`/api/v1/users/${userToDeleteId}`, {
                method: 'DELETE'
            });

            if (response.ok) {
                deleteUserModal?.hide();
                showToast('User deleted successfully', 'success');
                loadUsers();
            } else {
                const err = await response.json();
                showToast(err.detail || 'Failed to delete user', 'error');
            }
        } catch (error) {
            showToast('Connection error', 'error');
        } finally {
            setButtonTextLoading(btn, false, 'Deleting...', 'Delete User');
        }
    });
});

function setFormButtonLoading(baseName, isLoading) {
    const btn = document.getElementById(`${baseName}-btn`);
    const text = document.getElementById(`${baseName}-text`);
    const spinner = document.getElementById(`${baseName}-spinner`);

    if (btn) btn.disabled = isLoading;
    if (text) text.classList.toggle('d-none', isLoading);
    if (spinner) spinner.classList.toggle('d-none', !isLoading);
}

function setButtonTextLoading(button, isLoading, loadingText, defaultText) {
    if (!button) return;
    button.disabled = isLoading;
    button.textContent = isLoading ? loadingText : defaultText;
}

// Password strength calculator
function calculatePasswordStrength(password) {
    let score = 0;

    // Length
    if (password.length >= 8) score += 1;
    if (password.length >= 12) score += 1;

    // Complexity
    if (/[a-z]/.test(password)) score += 1;
    if (/[A-Z]/.test(password)) score += 1;
    if (/[0-9]/.test(password)) score += 1;
    if (/[^a-zA-Z0-9]/.test(password)) score += 1;

    return score;
}

function getStrengthClass(strength) {
    if (strength <= 2) return 'danger';
    if (strength <= 3) return 'warning';
    if (strength <= 4) return 'info';
    return 'success';
}

function getStrengthMessage(strength) {
    if (strength <= 2) return 'Weak password';
    if (strength <= 3) return 'Fair password';
    if (strength <= 4) return 'Good password';
    return 'Strong password';
}

// Error and success message handlers
function showMessage(type, message) {
    const div = document.getElementById(`${type}-message`);
    const text = document.getElementById(`${type}-text`);
    if (text) text.textContent = message;
    if (div) div.classList.remove('d-none');
}

function hideMessage(type) {
    const div = document.getElementById(`${type}-message`);
    if (div) div.classList.add('d-none');
}

function showError(message) {
    showMessage('error', message);
}

function hideError() {
    hideMessage('error');
}

function showSuccess(message) {
    showMessage('success', message);
}

function hideSuccess() {
    hideMessage('success');
}