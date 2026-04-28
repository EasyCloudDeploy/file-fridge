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
