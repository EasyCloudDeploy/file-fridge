/**
 * Storage Location Form JavaScript
 */

let locationId = null;
let isEditMode = false;
let storageLocationFormInitialized = false;
let googleCallbackUrl = '';

async function loadGoogleOAuthMetadata() {
    try {
        const response = await authenticatedFetch('/api/v1/storage/gdrive/oauth/metadata');
        if (!response.ok) {
            throw new Error('Failed to load Google OAuth metadata');
        }
        const data = await response.json();
        googleCallbackUrl = data.callback_url || '';
        const callbackInput = document.getElementById('gdrive_callback_url');
        if (callbackInput) {
            callbackInput.value = googleCallbackUrl;
        }
    } catch (_error) {
        const fallback = `${window.location.origin}/api/v1/storage/gdrive/oauth/callback`;
        googleCallbackUrl = fallback;
        const callbackInput = document.getElementById('gdrive_callback_url');
        if (callbackInput) {
            callbackInput.value = fallback;
        }
    }
}

async function copyGoogleCallbackUrl() {
    const callbackInput = document.getElementById('gdrive_callback_url');
    const value = callbackInput?.value?.trim() || googleCallbackUrl;
    if (!value) return;
    try {
        await navigator.clipboard.writeText(value);
        showToast('Google callback URL copied to clipboard.', 'success');
    } catch (_error) {
        showToast('Unable to copy automatically. Please copy the URL manually.', 'warning');
    }
}

function toggleBackendSections() {
    const backendType = document.getElementById('backend_type').value;
    const s3Section = document.getElementById('s3-config-section');
    const gdriveSection = document.getElementById('gdrive-config-section');
    const pathInput = document.getElementById('path');
    const browseBtn = document.querySelector("button[onclick*='targetInputId: \\\'path\\\'']");
    const allowOfflineSection = document.getElementById('allow-offline-section');
    const allowOfflineInput = document.getElementById('allow_offline');

    s3Section.classList.toggle('d-none', backendType !== 's3');
    gdriveSection.classList.toggle('d-none', backendType !== 'gdrive');

    const isLocal = backendType === 'local';
    pathInput.required = isLocal;
    pathInput.disabled = !isLocal;
    if (!isLocal) {
        pathInput.value = '';
    }
    if (browseBtn) {
        browseBtn.disabled = !isLocal;
    }
    if (allowOfflineSection) {
        allowOfflineSection.classList.toggle('d-none', !isLocal);
    }
    if (allowOfflineInput) {
        allowOfflineInput.disabled = !isLocal;
        if (!isLocal) {
            allowOfflineInput.checked = false;
        }
    }

    const operationMode = document.getElementById('operation_mode');
    const symlinkOption = operationMode.querySelector("option[value='symlink']");
    if (backendType === 'local') {
        symlinkOption.disabled = false;
    } else {
        if (operationMode.value === 'symlink') {
            operationMode.value = 'move';
        }
        symlinkOption.disabled = true;
    }

    const connectBtn = document.getElementById('gdrive-connect-btn');
    const connectStatus = document.getElementById('gdrive-connect-status');
    const localDriveSection = document.getElementById('local-drive-section');
    if (backendType !== 'gdrive') {
        connectBtn.disabled = true;
        connectStatus.textContent = 'Save first, then connect your Google account.';
    } else if (!isEditMode || !locationId) {
        connectBtn.disabled = true;
        connectStatus.textContent = 'Save this location first, then connect Google.';
    } else {
        connectBtn.disabled = false;
        connectStatus.textContent = 'Connect Google account to store refresh token securely.';
    }

    if (localDriveSection) {
        localDriveSection.classList.toggle('d-none', backendType !== 'local' || !isEditMode);
    }
}

function buildBackendConfig() {
    const backendType = document.getElementById('backend_type').value;
    const config = {};

    if (backendType === 's3') {
        const bucket = document.getElementById('s3_bucket').value.trim();
        const region = document.getElementById('s3_region').value.trim();
        const prefix = document.getElementById('s3_prefix').value.trim();
        const accessKeyId = document.getElementById('s3_access_key_id').value.trim();
        const secretAccessKey = document.getElementById('s3_secret_access_key').value.trim();
        const sessionToken = document.getElementById('s3_session_token').value.trim();
        const endpointUrl = document.getElementById('s3_endpoint_url').value.trim();

        if (!bucket && !isEditMode) {
            throw new Error('S3 bucket is required');
        }
        if (bucket) config.bucket = bucket;
        if (region) config.region = region;
        if (prefix) config.prefix = prefix;
        if (accessKeyId) config.access_key_id = accessKeyId;
        if (secretAccessKey) config.secret_access_key = secretAccessKey;
        if (sessionToken) config.session_token = sessionToken;
        if (endpointUrl) config.endpoint_url = endpointUrl;
    }

    if (backendType === 'gdrive') {
        const clientId = document.getElementById('gdrive_client_id').value.trim();
        const clientSecret = document.getElementById('gdrive_client_secret').value.trim();
        const folderId = document.getElementById('gdrive_folder_id').value.trim();

        if (!isEditMode && !clientId) {
            throw new Error('Google Drive client ID is required');
        }
        if (!clientSecret && !isEditMode) {
            throw new Error('Google Drive client secret is required');
        }

        if (clientId) config.client_id = clientId;
        if (clientSecret) config.client_secret = clientSecret;
        if (folderId) config.folder_id = folderId;
    }

    return config;
}

function populateBackendConfig(location) {
    const config = location.backend_config || {};

    document.getElementById('s3_bucket').value = config.bucket || '';
    document.getElementById('s3_region').value = config.region || '';
    document.getElementById('s3_prefix').value = config.prefix || '';
    document.getElementById('s3_access_key_id').value = '';
    document.getElementById('s3_secret_access_key').value = '';
    document.getElementById('s3_session_token').value = '';
    document.getElementById('s3_endpoint_url').value = config.endpoint_url || '';

    document.getElementById('gdrive_client_id').value = '';
    document.getElementById('gdrive_client_secret').value = '';
    document.getElementById('gdrive_folder_id').value = config.folder_id || '';
}

async function connectGoogleDrive() {
    if (!locationId) return;
    try {
        const response = await authenticatedFetch(`/api/v1/storage/locations/${locationId}/gdrive/oauth/start`, {
            method: 'POST'
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to start Google OAuth');
        }
        window.location.href = data.auth_url;
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function initStorageLocationForm() {
    if (storageLocationFormInitialized) {
        return;
    }
    storageLocationFormInitialized = true;

    await loadGoogleOAuthMetadata();

    document.getElementById('backend_type').addEventListener('change', toggleBackendSections);
    document.getElementById('gdrive-connect-btn').addEventListener('click', connectGoogleDrive);
    document.getElementById('copy-gdrive-callback-btn').addEventListener('click', copyGoogleCallbackUrl);

    // Check if we're in edit mode
    const urlParts = window.location.pathname.split('/');
    if (urlParts.includes('edit')) {
        isEditMode = true;
        locationId = parseInt(urlParts[urlParts.length - 2]);
        await loadLocation();
    } else {
        // Set up create mode title
        document.getElementById('form-title').innerHTML = '<i class="bi bi-plus-circle"></i> Create Storage Location';
        document.getElementById('submit-text').textContent = 'Create Location';
        toggleBackendSections();
    }

    const query = new URLSearchParams(window.location.search);
    if (query.get('gdrive_oauth') === 'success') {
        showToast('Google Drive account connected successfully.', 'success');
    }
    if (query.get('gdrive_oauth') === 'error') {
        showToast(`Google OAuth failed: ${query.get('reason') || 'unknown error'}`, 'error');
    }

    // Set up form submission
    document.getElementById('storage-location-form').addEventListener('submit', handleSubmit);
}

window.runWhenFileFridgeReady(() => {
    void initStorageLocationForm();
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
        document.getElementById('backend_type').value = location.backend_type || 'local';
        document.getElementById('operation_mode').value = location.operation_mode || 'move';
        document.getElementById('caution_threshold_percent').value = location.caution_threshold_percent || 20;
        document.getElementById('critical_threshold_percent').value = location.critical_threshold_percent || 10;
        document.getElementById('is_encrypted').checked = location.is_encrypted || false;
        document.getElementById('allow_offline').checked = location.allow_offline || false;

        populateBackendConfig(location);
        updateLocalDriveIdentity(location);
        toggleBackendSections();

    } catch (error) {
        console.error('Error loading storage location:', error);
        showToast(error.message, 'error');
    }
}

function updateLocalDriveIdentity(location) {
    const statusEl = document.getElementById('local-drive-status');
    const labelEl = document.getElementById('local-drive-label');
    const idEl = document.getElementById('local-drive-id');
    const mountEl = document.getElementById('local-drive-mount');
    const seenEl = document.getElementById('local-drive-seen');
    if (!statusEl || !labelEl || !idEl || !mountEl || !seenEl) return;

    const isConnected = Boolean(location.local_drive_is_connected);
    statusEl.textContent = isConnected ? 'Connected' : 'Offline';
    statusEl.className = isConnected ? 'text-success' : 'text-warning';
    labelEl.textContent = location.local_drive_label || 'N/A';
    idEl.textContent = location.local_drive_identifier || 'N/A';
    mountEl.textContent = location.local_drive_mount_path || 'N/A';
    seenEl.textContent = location.local_drive_last_seen_at
        ? new Date(location.local_drive_last_seen_at).toLocaleString()
        : 'N/A';
}

/**
 * Handle form submission
 */
async function handleSubmit(event) {
    event.preventDefault();

    const submitBtn = document.getElementById('submit-btn');
    const originalText = document.getElementById('submit-text').textContent;

    // Disable submit button
    submitBtn.disabled = true;
    document.getElementById('submit-text').textContent = 'Saving...';

    try {
        const backendType = document.getElementById('backend_type').value;
        const backendConfig = buildBackendConfig();

        const formData = {
            name: document.getElementById('name').value.trim(),
            path: document.getElementById('path').value.trim(),
            backend_type: backendType,
            operation_mode: document.getElementById('operation_mode').value,
            backend_config: backendConfig,
            caution_threshold_percent: parseInt(document.getElementById('caution_threshold_percent').value),
            critical_threshold_percent: parseInt(document.getElementById('critical_threshold_percent').value),
            allow_offline: backendType === 'local' && document.getElementById('allow_offline').checked,
            is_encrypted: document.getElementById('is_encrypted').checked
        };

        // Non-local backends derive path on the server.
        // For edit mode, omit path entirely to avoid sending an empty string
        // from the disabled path input.
        if (backendType !== 'local') {
            if (isEditMode) {
                delete formData.path;
            } else {
                formData.path = '/';
            }
        }

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

        const saved = await response.json();
        if (backendType === 'gdrive' && !isEditMode) {
            window.location.href = `/storage-locations/${saved.id}/edit`;
            return;
        }

        // Redirect to storage locations list
        window.location.href = '/storage-locations';

    } catch (error) {
        console.error('Error saving storage location:', error);
        showToast(error.message, 'error');

        // Re-enable submit button
        submitBtn.disabled = false;
        document.getElementById('submit-text').textContent = originalText;
    }
}

