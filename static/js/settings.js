// Settings page JavaScript

document.addEventListener('DOMContentLoaded', function() {
    // Tab switching
    const navLinks = document.querySelectorAll('#settings-nav .list-group-item');
    const sections = document.querySelectorAll('.settings-section');

    navLinks.forEach(link => {
        link.addEventListener('click', function(e) {
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
                loadRemoteConnections();
                fetchConnectionCode();
                loadRemoteTransfers();
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
        togglePasswordBtn.addEventListener('click', function() {
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
        newPasswordInputWithStrength.addEventListener('input', function() {
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

    // Password change form submission
    const changePasswordForm = document.getElementById('change-password-form');

    if (changePasswordForm) {
        changePasswordForm.addEventListener('submit', async function(e) {
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

    // Fetch and display connection code
    async function fetchConnectionCode() {
        try {
            const response = await authenticatedFetch('/api/remote/connection-code');
            if (response.ok) {
                const data = await response.json();
                document.getElementById('my-connection-code').value = data.code;
            }
        } catch (error) {
            console.error('Error fetching connection code:', error);
        }
    }

    const refreshCodeBtn = document.getElementById('refresh-code-btn');
    if (refreshCodeBtn) {
        refreshCodeBtn.addEventListener('click', fetchConnectionCode);
    }

    const copyCodeBtn = document.getElementById('copy-code-btn');
    if (copyCodeBtn) {
        copyCodeBtn.addEventListener('click', async function() {
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
        try {
            const response = await authenticatedFetch('/api/remote/connections');
            if (response.ok) {
                const connections = await response.json();
                list.innerHTML = '';

                if (connections.length === 0) {
                    list.innerHTML = '<tr><td colspan="4" class="text-center text-muted py-4">No remote connections found.</td></tr>';
                    return;
                }

                connections.forEach(conn => {
                    const date = new Date(conn.created_at).toLocaleString();
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td>${conn.name}</td>
                        <td>${conn.url}</td>
                        <td>${date}</td>
                        <td>
                            <button class="btn btn-sm btn-outline-danger delete-conn-btn" data-id="${conn.id}" data-name="${conn.name}">
                                <i class="bi bi-trash"></i>
                            </button>
                        </td>
                    `;
                    list.appendChild(tr);
                });

                // Add delete event listeners
                document.querySelectorAll('.delete-conn-btn').forEach(btn => {
                    btn.addEventListener('click', function() {
                        const id = this.dataset.id;
                        const name = this.dataset.name;
                        showDeleteModal(id, name);
                    });
                });
            }
        } catch (error) {
            console.error('Error loading connections:', error);
            list.innerHTML = '<tr><td colspan="4" class="text-center text-danger py-4">Failed to load connections.</td></tr>';
        }
    }

    // Add connection form
    const addConnectionForm = document.getElementById('add-connection-form');
    if (addConnectionForm) {
        addConnectionForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            setFormButtonLoading('save-connection', true);

            const formData = new FormData(this);
            const data = Object.fromEntries(formData.entries());

            try {
                const response = await authenticatedFetch('/api/remote/connect', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });

                if (response.ok) {
                    bootstrap.Modal.getInstance(document.getElementById('addConnectionModal')).hide();
                    addConnectionForm.reset();
                    loadRemoteConnections();
                } else {
                    const errorData = await response.json();
                    alert('Error: ' + (errorData.detail || 'Failed to add connection'));
                }
            } catch (error) {
                console.error('Error adding connection:', error);
                alert('Failed to connect to server.');
            } finally {
                setFormButtonLoading('save-connection', false);
            }
        });
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

    document.getElementById('force-delete-check').addEventListener('change', function() {
        const warning = document.getElementById('force-delete-warning');
        if (this.checked) {
            warning.classList.remove('d-none');
        } else {
            warning.classList.add('d-none');
        }
    });

    // Load remote transfers
    async function loadRemoteTransfers() {
        const list = document.getElementById('remote-transfers-list');
        try {
            const response = await authenticatedFetch('/api/remote/transfers');
            if (response.ok) {
                const transfers = await response.json();
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

                    const canCancel = ['pending', 'in_progress', 'failed'].includes(job.status);
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td title="${job.source_path}">${fileName}</td>
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

    globalThis.cancelTransfer = async function(jobId) {
        if (!confirm('Are you sure you want to cancel this transfer?')) return;

        try {
            const response = await authenticatedFetch(`/api/remote/transfers/${jobId}/cancel`, {
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

    globalThis.bulkCancelTransfers = async function() {
        const failedJobs = transfers.filter(t => ['failed', 'pending'].includes(t.status));
        if (failedJobs.length === 0) {
            alert('No failed or pending transfers to cancel.');
            return;
        }

        if (!confirm(`Cancel ${failedJobs.length} transfers?`)) return;

        try {
            const jobIds = failedJobs.map(t => t.id);
            const response = await authenticatedFetch('/api/remote/transfers/bulk/cancel', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ job_ids: jobIds })
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

    globalThis.bulkRetryTransfers = async function() {
        const failedJobs = transfers.filter(t => t.status === 'failed');
        if (failedJobs.length === 0) {
            alert('No failed transfers to retry.');
            return;
        }

        if (!confirm(`Retry ${failedJobs.length} transfers?`)) return;

        try {
            const jobIds = failedJobs.map(t => t.id);
            const response = await authenticatedFetch('/api/remote/transfers/bulk/retry', {
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

    // Auto-refresh transfers if visible
    setInterval(() => {
        const section = document.getElementById('remote-connections-section');
        if (section && !section.classList.contains('d-none')) {
            loadRemoteTransfers();
        }
    }, 5000);

    document.getElementById('confirm-delete-conn-btn').addEventListener('click', async function() {
        if (!connectionToDelete) return;

        const button = this;
        setButtonTextLoading(button, true, 'Deleting...', 'Delete');

        try {
            const force = document.getElementById('force-delete-check').checked;
            const response = await authenticatedFetch(`/api/remote/connections/${connectionToDelete}?force=${force}`, {
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