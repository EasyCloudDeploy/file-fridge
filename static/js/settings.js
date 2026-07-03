// Settings page JavaScript

let settingsPageInitialized = false;

function initSettingsPage() {
    if (settingsPageInitialized) {
        return;
    }
    settingsPageInitialized = true;

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

            // Initial load for SMTP if clicked
            if (sectionId === 'smtp') {
                loadSmtpConfig();
            }

            // Initial load for OIDC if clicked
            if (sectionId === 'oidc') {
                loadOidcConfig();
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
                showToast('Token copied to clipboard', 'success');
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

    // P2P settings are handled exclusively in /static/js/p2p_settings.js.
    function checkRemoteConfiguration() {}
    async function loadEncryptionKeys() {
        const list = document.getElementById('encryption-keys-list');
        if (!list) return;

        // Check file migration status
        checkFileKeyMigrationStatus();

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
                        deleteEncryptionKey(this.dataset.id, this);
                    });
                });
            }
        } catch (error) {
            console.error('Error loading encryption keys:', error);
            list.innerHTML = '<tr><td colspan="4" class="text-center text-danger py-4">Failed to load encryption keys.</td></tr>';
        }
    }

    async function deleteEncryptionKey(keyId, btn) {
        const confirmed = await showConfirmModal({
            title: 'Delete Encryption Key',
            message: 'Are you sure you want to delete this encryption key? This cannot be undone. Any data encrypted EXCLUSIVELY with this key will become unreadable and its password field will be cleared.',
            confirmText: 'Delete Key',
            dangerous: true
        });

        if (!confirmed) {
            return;
        }

        const originalContent = btn ? btn.innerHTML : '';
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
        }

        try {
            const response = await authenticatedFetch(`/api/v1/encryption/keys/${keyId}`, {
                method: 'DELETE'
            });

            if (response.ok) {
                showToast('Encryption key deleted', 'success');
                loadEncryptionKeys();
            } else {
                const errorData = await response.json();
                showToast(errorData.detail || 'Failed to delete encryption key', 'error');
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = originalContent;
                }
            }
        } catch (error) {
            console.error('Error deleting encryption key:', error);
            showToast('Failed to connect to server', 'error');
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = originalContent;
            }
        }
    }

    const btnGenerateKey = document.getElementById('btn-generate-key');
    if (btnGenerateKey) {
        btnGenerateKey.addEventListener('click', async function () {
            const confirmed = await showConfirmModal({
                title: 'Rotate Encryption Key',
                message: 'Are you sure you want to generate a new encryption key? This will rotate the current active key. New data will use this key, while existing data remains readable using old keys.',
                confirmText: 'Rotate Key',
                dangerous: false
            });

            if (!confirmed) {
                return;
            }

            const originalContent = this.innerHTML;
            this.disabled = true;
            this.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Rotating...';

            try {
                const response = await authenticatedFetch('/api/v1/encryption/keys', {
                    method: 'POST'
                });

                if (response.ok) {
                    showToast('Encryption key rotated successfully', 'success');
                    loadEncryptionKeys();
                } else {
                    const errorData = await response.json();
                    showToast(errorData.detail || 'Failed to generate encryption key', 'error');
                }
            } catch (error) {
                console.error('Error generating encryption key:', error);
                showToast('Failed to connect to server', 'error');
            } finally {
                this.disabled = false;
                this.innerHTML = originalContent;
            }
        });
    }

    let migrationTimeoutId = null;

    async function checkFileKeyMigrationStatus() {
        const banner = document.getElementById('file-migration-status-banner');
        const idleBanner = document.getElementById('file-migration-idle-banner');
        const progressBar = document.getElementById('file-migration-progress-bar');
        const progressText = document.getElementById('file-migration-progress-text');
        const btnRotate = document.getElementById('btn-rotate-file-key');

        if (!banner || !idleBanner || !progressBar || !progressText || !btnRotate) {
            return;
        }

        try {
            const response = await authenticatedFetch('/api/v1/encryption/keys/migration-status');
            if (response.ok) {
                const data = await response.json();
                if (data.in_progress) {
                    banner.classList.remove('d-none');
                    idleBanner.classList.add('d-none');
                    btnRotate.disabled = true;

                    const total = data.total || 0;
                    const progress = data.progress || 0;
                    const percent = total > 0 ? Math.round((progress / total) * 100) : 0;

                    progressBar.style.width = `${percent}%`;
                    progressBar.setAttribute('aria-valuenow', percent);
                    progressBar.textContent = `${percent}%`;
                    progressText.textContent = `${progress} / ${total} files migrated`;

                    // Poll again in 2 seconds
                    if (migrationTimeoutId) {
                        clearTimeout(migrationTimeoutId);
                    }
                    migrationTimeoutId = setTimeout(checkFileKeyMigrationStatus, 2000);
                } else {
                    banner.classList.add('d-none');
                    idleBanner.classList.remove('d-none');
                    btnRotate.disabled = false;
                    if (migrationTimeoutId) {
                        clearTimeout(migrationTimeoutId);
                        migrationTimeoutId = null;
                    }
                }
            }
        } catch (error) {
            console.error('Error checking file migration status:', error);
        }
    }

    const btnRotateFileKey = document.getElementById('btn-rotate-file-key');
    if (btnRotateFileKey) {
        btnRotateFileKey.addEventListener('click', async function () {
            const confirmed = await showConfirmModal({
                title: 'Rotate File Encryption Key',
                message: 'Are you sure you want to rotate the file encryption root key and migrate all cold storage files? This will generate a new root key and trigger a background task to decrypt and re-encrypt all existing cold storage files. Files remain readable using the fallback key during this process.',
                confirmText: 'Rotate & Migrate',
                dangerous: false
            });

            if (!confirmed) {
                return;
            }

            const originalContent = this.innerHTML;
            this.disabled = true;
            this.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Starting...';

            try {
                const response = await authenticatedFetch('/api/v1/encryption/keys/rotate-file-key', {
                    method: 'POST'
                });

                if (response.ok) {
                    showToast('File encryption key rotated and migration started', 'success');
                    // Trigger immediate status check to update UI & start polling
                    await checkFileKeyMigrationStatus();
                } else {
                    const errorData = await response.json();
                    showToast(errorData.detail || 'Failed to rotate file encryption key', 'error');
                    this.disabled = false;
                    this.innerHTML = originalContent;
                }
            } catch (error) {
                console.error('Error rotating file encryption key:', error);
                showToast('Failed to connect to server', 'error');
                this.disabled = false;
                this.innerHTML = originalContent;
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

            // If the hash is #oidc, load the OIDC config
            if (globalThis.location.hash === '#oidc') {
                loadOidcConfig();
            }
        }

        if (payload.roles && (payload.roles.includes('admin') || payload.roles.includes('manager'))) {
            // Show notifiers-only elements
            document.querySelectorAll('.notifiers-only').forEach(el => el.classList.remove('d-none'));

            // If the hash is #smtp, load the SMTP config
            if (globalThis.location.hash === '#smtp') {
                loadSmtpConfig();
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

    // --- SMTP Server Configuration ---

    async function loadSmtpConfig() {
        const warningBanner = document.getElementById('smtp-env-warning');
        const form = document.getElementById('smtp-config-form');
        if (!form) return;

        const inputs = [
            document.getElementById('global-smtp-host'),
            document.getElementById('global-smtp-port'),
            document.getElementById('global-smtp-user'),
            document.getElementById('global-smtp-password'),
            document.getElementById('global-smtp-sender'),
            document.getElementById('global-smtp-use-tls')
        ];
        const saveBtn = document.getElementById('save-smtp-btn');

        try {
            const response = await authenticatedFetch('/api/v1/settings/config');
            if (response.ok) {
                const config = await response.json();
                const smtp = config.smtp || {};

                // Hydrate inputs
                document.getElementById('global-smtp-host').value = smtp.smtp_host?.value || '';
                document.getElementById('global-smtp-port').value = smtp.smtp_port?.value ?? 587;
                document.getElementById('global-smtp-user').value = smtp.smtp_user?.value || '';
                document.getElementById('global-smtp-sender').value = smtp.smtp_sender?.value || '';
                document.getElementById('global-smtp-use-tls').checked = smtp.smtp_use_tls?.value !== false;

                // Handle password placeholder
                const passwordInput = document.getElementById('global-smtp-password');
                if (passwordInput) {
                    if (smtp.smtp_host?.value) {
                        passwordInput.placeholder = '••••••••';
                    } else {
                        passwordInput.placeholder = 'Password for authentication (optional)';
                    }
                    passwordInput.value = '';
                }

                // Handle environment lock
                if (smtp.is_env_configured) {
                    warningBanner?.classList.remove('d-none');
                    inputs.forEach(input => {
                        if (input) input.disabled = true;
                    });
                    if (saveBtn) saveBtn.disabled = true;
                } else {
                    warningBanner?.classList.add('d-none');
                    inputs.forEach(input => {
                        if (input) input.disabled = false;
                    });
                    if (saveBtn) saveBtn.disabled = false;
                }
            } else {
                showToast('Failed to load global SMTP configuration', 'error');
            }
        } catch (error) {
            console.error('Error loading SMTP config:', error);
            showToast('Failed to connect to server for SMTP settings', 'error');
        }
    }

    const smtpConfigForm = document.getElementById('smtp-config-form');
    if (smtpConfigForm) {
        smtpConfigForm.addEventListener('submit', async function (e) {
            e.preventDefault();

            const smtpHost = document.getElementById('global-smtp-host').value || null;
            const smtpPort = parseInt(document.getElementById('global-smtp-port').value) || 587;
            const smtpUser = document.getElementById('global-smtp-user').value || null;
            const smtpPasswordInput = document.getElementById('global-smtp-password').value;
            const smtpPassword = smtpPasswordInput === '' ? null : smtpPasswordInput;
            const smtpSender = document.getElementById('global-smtp-sender').value || null;
            const smtpUseTls = document.getElementById('global-smtp-use-tls').checked;

            const payload = {
                smtp_host: smtpHost,
                smtp_port: smtpPort,
                smtp_user: smtpUser,
                smtp_sender: smtpSender,
                smtp_use_tls: smtpUseTls
            };
            if (smtpPassword !== null) {
                payload.smtp_password = smtpPassword;
            }

            const successMsg = document.getElementById('smtp-success-message');
            const successText = document.getElementById('smtp-success-text');
            const errorMsg = document.getElementById('smtp-error-message');
            const errorText = document.getElementById('smtp-error-text');

            successMsg?.classList.add('d-none');
            errorMsg?.classList.add('d-none');

            setFormButtonLoading('save-smtp', true);

            try {
                const response = await authenticatedFetch('/api/v1/settings/smtp', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                if (response.ok) {
                    if (successMsg && successText) {
                        successText.textContent = 'Global SMTP configuration updated successfully!';
                        successMsg.classList.remove('d-none');
                    }
                    showToast('Global SMTP settings saved successfully', 'success');
                    await loadSmtpConfig();
                } else {
                    const data = await response.json();
                    if (errorMsg && errorText) {
                        errorText.textContent = data.detail || 'Failed to update SMTP settings.';
                        errorMsg.classList.remove('d-none');
                    }
                    showToast(data.detail || 'Failed to update SMTP settings', 'error');
                }
            } catch (error) {
                console.error('SMTP configuration error:', error);
                if (errorMsg && errorText) {
                    errorText.textContent = 'Connection error. Please try again.';
                    errorMsg.classList.remove('d-none');
                }
                showToast('Failed to connect to server', 'error');
            } finally {
                setFormButtonLoading('save-smtp', false);
            }
        });
    }

    async function loadOidcConfig() {
        const warningBanner = document.getElementById('oidc-env-warning');
        const form = document.getElementById('oidc-config-form');
        if (!form) return;

        const inputs = [
            document.getElementById('oidc-enabled'),
            document.getElementById('oidc-provider-name-input'),
            document.getElementById('oidc-issuer'),
            document.getElementById('oidc-client-id'),
            document.getElementById('oidc-client-secret'),
            document.getElementById('oidc-redirect-uri'),
            document.getElementById('oidc-roles-claim'),
            document.getElementById('oidc-default-roles'),
            document.getElementById('oidc-admin-group'),
            document.getElementById('oidc-manager-group'),
            document.getElementById('oidc-viewer-group')
        ];
        const saveBtn = document.getElementById('save-oidc-btn');

        try {
            const response = await authenticatedFetch('/api/v1/settings/config');
            if (response.ok) {
                const config = await response.json();
                const oidc = config.oidc || {};

                // Hydrate inputs
                document.getElementById('oidc-enabled').checked = oidc.oidc_enabled?.value === true;
                document.getElementById('oidc-provider-name-input').value = oidc.oidc_provider_name?.value || '';
                document.getElementById('oidc-issuer').value = oidc.oidc_issuer?.value || '';
                document.getElementById('oidc-client-id').value = oidc.oidc_client_id?.value || '';
                document.getElementById('oidc-redirect-uri').value = oidc.oidc_redirect_uri?.value || '';
                document.getElementById('oidc-roles-claim').value = oidc.oidc_roles_claim?.value || '';
                document.getElementById('oidc-default-roles').value = oidc.oidc_default_roles?.value || '';
                document.getElementById('oidc-admin-group').value = oidc.oidc_admin_group?.value || '';
                document.getElementById('oidc-manager-group').value = oidc.oidc_manager_group?.value || '';
                document.getElementById('oidc-viewer-group').value = oidc.oidc_viewer_group?.value || '';

                // Handle client secret placeholder
                const secretInput = document.getElementById('oidc-client-secret');
                if (secretInput) {
                    if (oidc.oidc_client_id?.value) {
                        secretInput.placeholder = '••••••••';
                    } else {
                        secretInput.placeholder = 'Enter your Client Secret';
                    }
                    secretInput.value = '';
                }

                // Handle environment lock
                if (oidc.is_env_configured) {
                    warningBanner?.classList.remove('d-none');
                    inputs.forEach(input => {
                        if (input) input.disabled = true;
                    });
                    if (saveBtn) saveBtn.disabled = true;
                } else {
                    warningBanner?.classList.add('d-none');
                    inputs.forEach(input => {
                        if (input) input.disabled = false;
                    });
                    if (saveBtn) saveBtn.disabled = false;
                }
            } else {
                showToast('Failed to load global OIDC configuration', 'error');
            }
        } catch (error) {
            console.error('Error loading OIDC config:', error);
            showToast('Failed to connect to server for OIDC settings', 'error');
        }
    }

    const oidcConfigForm = document.getElementById('oidc-config-form');
    if (oidcConfigForm) {
        oidcConfigForm.addEventListener('submit', async function (e) {
            e.preventDefault();

            const oidcEnabled = document.getElementById('oidc-enabled').checked;
            const oidcProviderName = document.getElementById('oidc-provider-name-input').value || null;
            const oidcIssuer = document.getElementById('oidc-issuer').value || null;
            const oidcClientId = document.getElementById('oidc-client-id').value || null;
            const secretInputVal = document.getElementById('oidc-client-secret').value;
            const oidcClientSecret = secretInputVal === '' ? null : secretInputVal;
            const oidcRedirectUri = document.getElementById('oidc-redirect-uri').value || null;
            const oidcRolesClaim = document.getElementById('oidc-roles-claim').value || null;
            const oidcDefaultRoles = document.getElementById('oidc-default-roles').value || null;
            const oidcAdminGroup = document.getElementById('oidc-admin-group').value || null;
            const oidcManagerGroup = document.getElementById('oidc-manager-group').value || null;
            const oidcViewerGroup = document.getElementById('oidc-viewer-group').value || null;

            const payload = {
                oidc_enabled: oidcEnabled,
                oidc_provider_name: oidcProviderName,
                oidc_issuer: oidcIssuer,
                oidc_client_id: oidcClientId,
                oidc_redirect_uri: oidcRedirectUri,
                oidc_roles_claim: oidcRolesClaim,
                oidc_default_roles: oidcDefaultRoles,
                oidc_admin_group: oidcAdminGroup,
                oidc_manager_group: oidcManagerGroup,
                oidc_viewer_group: oidcViewerGroup
            };

            if (oidcClientSecret !== null) {
                payload.oidc_client_secret = oidcClientSecret;
            }

            const successMsg = document.getElementById('oidc-success-message');
            const successText = document.getElementById('oidc-success-text');
            const errorMsg = document.getElementById('oidc-error-message');
            const errorText = document.getElementById('oidc-error-text');

            successMsg?.classList.add('d-none');
            errorMsg?.classList.add('d-none');

            setFormButtonLoading('save-oidc', true);

            try {
                const response = await authenticatedFetch('/api/v1/settings/oidc', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                if (response.ok) {
                    if (successMsg && successText) {
                        successText.textContent = 'Global OIDC configuration updated successfully!';
                        successMsg.classList.remove('d-none');
                    }
                    showToast('Global OIDC settings saved successfully', 'success');
                    await loadOidcConfig();
                } else {
                    const data = await response.json();
                    if (errorMsg && errorText) {
                        errorText.textContent = data.detail || 'Failed to update OIDC settings.';
                        errorMsg.classList.remove('d-none');
                    }
                    showToast(data.detail || 'Failed to update OIDC settings', 'error');
                }
            } catch (error) {
                console.error('OIDC configuration error:', error);
                if (errorMsg && errorText) {
                    errorText.textContent = 'Connection error. Please try again.';
                    errorMsg.classList.remove('d-none');
                }
                showToast('Failed to connect to server', 'error');
            } finally {
                setFormButtonLoading('save-oidc', false);
            }
        });
    }
}

window.runWhenFileFridgeReady(initSettingsPage);

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
