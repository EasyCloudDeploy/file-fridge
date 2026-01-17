/**
 * Tag Management JavaScript
 */

let allTags = [];
let currentEditingTagId = null;
let tagModal = null;
let deleteTagModal = null;

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', async () => {
    // Initialize Bootstrap modals
    tagModal = new bootstrap.Modal(document.getElementById('tagModal'));
    deleteTagModal = new bootstrap.Modal(document.getElementById('deleteTagModal'));

    // Set up event listeners
    setupEventListeners();

    // Load tags
    await loadTags();
});

/**
 * Set up all event listeners
 */
function setupEventListeners() {
    // Create tag button
    document.getElementById('create_tag_btn').addEventListener('click', () => {
        openTagModal();
    });

    // Save tag button
    document.getElementById('save_tag_btn').addEventListener('click', () => {
        saveTag();
    });

    // Confirm delete button
    document.getElementById('confirm_delete_tag_btn').addEventListener('click', () => {
        confirmDeleteTag();
    });

    // Color picker sync
    const colorPicker = document.getElementById('tag_color');
    const colorText = document.getElementById('tag_color_text');
    const tagNameInput = document.getElementById('tag_name');
    const tagPreview = document.getElementById('tag_preview');

    colorPicker.addEventListener('input', (e) => {
        const color = e.target.value;
        colorText.value = color;
        tagPreview.style.backgroundColor = color;
    });

    // Update preview when tag name changes
    tagNameInput.addEventListener('input', (e) => {
        const name = e.target.value || 'Tag Preview';
        tagPreview.textContent = name;
    });

    // Form submission with Enter key
    document.getElementById('tag_form').addEventListener('submit', (e) => {
        e.preventDefault();
        saveTag();
    });
}

/**
 * Load all tags from the API
 */
async function loadTags() {
    const loadingEl = document.getElementById('tags_loading');
    const contentEl = document.getElementById('tags_content');

    try {
        loadingEl.style.display = 'block';
        contentEl.style.display = 'none';

        const response = await authenticatedFetch(`/api/v1/tags`);
        if (!response.ok) {
            throw new Error(`Failed to load tags: ${response.statusText}`);
        }

        allTags = await response.json();

        loadingEl.style.display = 'none';
        contentEl.style.display = 'block';

        renderTags();
    } catch (error) {
        console.error('Error loading tags:', error);
        loadingEl.innerHTML = `
            <div class="alert alert-danger" role="alert">
                <i class="bi bi-exclamation-triangle"></i>
                Failed to load tags: ${error.message}
            </div>
        `;
    }
}

/**
 * Render tags table
 */
function renderTags() {
    const emptyEl = document.getElementById('tags_empty');
    const tableContainer = document.getElementById('tags_table_container');
    const tableBody = document.getElementById('tags_table_body');

    if (allTags.length === 0) {
        emptyEl.style.display = 'block';
        tableContainer.style.display = 'none';
        return;
    }

    emptyEl.style.display = 'none';
    tableContainer.style.display = 'block';

    tableBody.innerHTML = allTags.map(tag => {
        const color = tag.color || '#6c757d';
        const createdDate = tag.created_at ? new Date(tag.created_at).toLocaleDateString() : 'N/A';
        const fileCount = tag.file_count || 0;

        return `
            <tr>
                <td>
                    <div style="width: 24px; height: 24px; background-color: ${color}; border-radius: 4px; border: 1px solid #dee2e6;"></div>
                </td>
                <td>
                    <span class="badge" style="background-color: ${color};">${escapeHtml(tag.name)}</span>
                </td>
                <td class="d-none d-md-table-cell">${escapeHtml(tag.description || '')}</td>
                <td>
                    <span class="badge bg-secondary">${fileCount}</span>
                </td>
                <td class="d-none d-lg-table-cell">${createdDate}</td>
                <td>
                    <button class="btn btn-sm btn-outline-primary" onclick="editTag(${tag.id})" title="Edit tag">
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteTag(${tag.id})" title="Delete tag">
                        <i class="bi bi-trash"></i>
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

/**
 * Open tag modal for creating/editing
 */
function openTagModal(tagId = null) {
    const modalTitle = document.getElementById('tagModalLabel');
    const tagIdInput = document.getElementById('tag_id');
    const tagNameInput = document.getElementById('tag_name');
    const tagDescInput = document.getElementById('tag_description');
    const tagColorInput = document.getElementById('tag_color');
    const tagColorText = document.getElementById('tag_color_text');
    const tagPreview = document.getElementById('tag_preview');
    const errorEl = document.getElementById('tag_error');

    // Clear error
    errorEl.classList.add('d-none');
    errorEl.textContent = '';

    if (tagId) {
        // Edit mode
        const tag = allTags.find(t => t.id === tagId);
        if (!tag) {
            console.error('Tag not found:', tagId);
            return;
        }

        modalTitle.textContent = 'Edit Tag';
        tagIdInput.value = tag.id;
        tagNameInput.value = tag.name;
        tagDescInput.value = tag.description || '';
        tagColorInput.value = tag.color || '#0d6efd';
        tagColorText.value = tag.color || '#0d6efd';
        tagPreview.textContent = tag.name;
        tagPreview.style.backgroundColor = tag.color || '#0d6efd';
        currentEditingTagId = tagId;
    } else {
        // Create mode
        modalTitle.textContent = 'Create Tag';
        tagIdInput.value = '';
        tagNameInput.value = '';
        tagDescInput.value = '';
        tagColorInput.value = '#0d6efd';
        tagColorText.value = '#0d6efd';
        tagPreview.textContent = 'Tag Preview';
        tagPreview.style.backgroundColor = '#0d6efd';
        currentEditingTagId = null;
    }

    tagModal.show();
}

/**
 * Save tag (create or update)
 */
async function saveTag() {
    const saveBtn = document.getElementById('save_tag_btn');
    const saveSpinner = document.getElementById('save_tag_spinner');
    const saveText = document.getElementById('save_tag_text');
    const errorEl = document.getElementById('tag_error');

    const tagId = document.getElementById('tag_id').value;
    const name = document.getElementById('tag_name').value.trim();
    const description = document.getElementById('tag_description').value.trim();
    const color = document.getElementById('tag_color').value;

    // Validate
    if (!name) {
        showError(errorEl, 'Tag name is required');
        return;
    }

    // Show loading state
    saveBtn.disabled = true;
    saveSpinner.classList.remove('d-none');
    saveText.textContent = tagId ? 'Updating...' : 'Creating...';
    errorEl.classList.add('d-none');

    try {
        let response;
        const payload = {
            name: name,
            description: description || null,
            color: color
        };

        if (tagId) {
            // Update existing tag
            response = await authenticatedFetch(`/api/v1/tags/${tagId}`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });
        } else {
            // Create new tag
            response = await authenticatedFetch(`/api/v1/tags`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });
        }

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || `Failed to save tag: ${response.statusText}`);
        }

        // Success - reload tags and close modal
        await loadTags();
        tagModal.hide();

        // Reset form
        document.getElementById('tag_form').reset();

    } catch (error) {
        console.error('Error saving tag:', error);
        showError(errorEl, error.message);
    } finally {
        saveBtn.disabled = false;
        saveSpinner.classList.add('d-none');
        saveText.textContent = 'Save Tag';
    }
}

/**
 * Edit tag
 */
function editTag(tagId) {
    openTagModal(tagId);
}

/**
 * Delete tag (show confirmation)
 */
function deleteTag(tagId) {
    const tag = allTags.find(t => t.id === tagId);
    if (!tag) {
        console.error('Tag not found:', tagId);
        return;
    }

    currentEditingTagId = tagId;
    document.getElementById('delete_tag_name').textContent = tag.name;
    deleteTagModal.show();
}

/**
 * Confirm delete tag
 */
async function confirmDeleteTag() {
    const deleteBtn = document.getElementById('confirm_delete_tag_btn');
    const deleteSpinner = document.getElementById('delete_tag_spinner');
    const deleteText = document.getElementById('delete_tag_text');

    if (!currentEditingTagId) {
        console.error('No tag selected for deletion');
        return;
    }

    // Show loading state
    deleteBtn.disabled = true;
    deleteSpinner.classList.remove('d-none');
    deleteText.textContent = 'Deleting...';

    try {
        const response = await authenticatedFetch(`/api/v1/tags/${currentEditingTagId}`, {
            method: 'DELETE'
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || `Failed to delete tag: ${response.statusText}`);
        }

        // Success - reload tags and close modal
        await loadTags();
        deleteTagModal.hide();

    } catch (error) {
        console.error('Error deleting tag:', error);
        alert(`Failed to delete tag: ${error.message}`);
    } finally {
        deleteBtn.disabled = false;
        deleteSpinner.classList.add('d-none');
        deleteText.textContent = 'Delete Tag';
        currentEditingTagId = null;
    }
}

/**
 * Show error message in an element
 */
function showError(element, message) {
    element.textContent = message;
    element.classList.remove('d-none');
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ========================================
// TAG RULES FUNCTIONALITY
// ========================================

let allRules = [];
let currentEditingRuleId = null;
let tagRuleModal = null;
let deleteRuleModal = null;

/**
 * Load all tag rules
 */
async function loadTagRules() {
    const loadingEl = document.getElementById('rules_loading');
    const contentEl = document.getElementById('rules_content');

    try {
        loadingEl.style.display = 'block';
        contentEl.style.display = 'none';

        const response = await authenticatedFetch('/api/v1/tag-rules');
        if (!response.ok) {
            throw new Error(`Failed to load tag rules: ${response.statusText}`);
        }

        allRules = await response.json();

        loadingEl.style.display = 'none';
        contentEl.style.display = 'block';

        renderTagRules();
    } catch (error) {
        console.error('Error loading tag rules:', error);
        loadingEl.innerHTML = `
            <div class="alert alert-danger" role="alert">
                <i class="bi bi-exclamation-triangle"></i>
                Failed to load tag rules: ${error.message}
            </div>
        `;
    }
}

/**
 * Render tag rules table
 */
function renderTagRules() {
    const emptyEl = document.getElementById('rules_empty');
    const tableContainer = document.getElementById('rules_table_container');
    const tableBody = document.getElementById('rules_table_body');

    if (allRules.length === 0) {
        emptyEl.style.display = 'block';
        tableContainer.style.display = 'none';
        return;
    }

    emptyEl.style.display = 'none';
    tableContainer.style.display = 'block';

    tableBody.innerHTML = allRules.map(rule => {
        const tag = rule.tag || {};
        const tagColor = tag.color || '#6c757d';
        const tagName = tag.name || 'Unknown';
        const enabledBadge = rule.enabled
            ? '<span class="badge bg-success"><span class="d-none d-sm-inline">Enabled</span><i class="bi bi-check d-sm-none"></i></span>'
            : '<span class="badge bg-secondary"><span class="d-none d-sm-inline">Disabled</span><i class="bi bi-x d-sm-none"></i></span>';

        return `
            <tr>
                <td>${enabledBadge}</td>
                <td><span class="badge" style="background-color: ${tagColor};">${escapeHtml(tagName)}</span></td>
                <td class="d-none d-md-table-cell"><code>${escapeHtml(rule.criterion_type)}</code></td>
                <td class="d-none d-lg-table-cell"><code>${escapeHtml(rule.operator)}</code></td>
                <td><code class="small">${escapeHtml(rule.value)}</code></td>
                <td class="d-none d-md-table-cell">${rule.priority}</td>
                <td>
                    <button class="btn btn-sm btn-outline-primary" onclick="editTagRule(${rule.id})" title="Edit rule">
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteTagRule(${rule.id})" title="Delete rule">
                        <i class="bi bi-trash"></i>
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

/**
 * Open tag rule modal
 */
function openTagRuleModal(ruleId = null) {
    const modalTitle = document.getElementById('tagRuleModalLabel');
    const ruleIdInput = document.getElementById('rule_id');
    const errorEl = document.getElementById('rule_error');

    // Clear error
    errorEl.classList.add('d-none');
    errorEl.textContent = '';

    // Populate tag dropdown
    const tagSelect = document.getElementById('rule_tag_id');
    tagSelect.innerHTML = '<option value="">Select a tag...</option>' +
        allTags.map(tag => `<option value="${tag.id}">${escapeHtml(tag.name)}</option>`).join('');

    if (ruleId) {
        // Edit mode
        const rule = allRules.find(r => r.id === ruleId);
        if (!rule) {
            console.error('Rule not found:', ruleId);
            return;
        }

        modalTitle.textContent = 'Edit Tag Rule';
        ruleIdInput.value = rule.id;
        document.getElementById('rule_tag_id').value = rule.tag_id;
        document.getElementById('rule_criterion_type').value = rule.criterion_type;
        document.getElementById('rule_operator').value = rule.operator;
        document.getElementById('rule_value').value = rule.value;
        document.getElementById('rule_priority').value = rule.priority;
        document.getElementById('rule_enabled').checked = rule.enabled;
        currentEditingRuleId = ruleId;
    } else {
        // Create mode
        modalTitle.textContent = 'Create Tag Rule';
        ruleIdInput.value = '';
        document.getElementById('rule_form').reset();
        document.getElementById('rule_priority').value = 0;
        document.getElementById('rule_enabled').checked = true;
        currentEditingRuleId = null;
    }

    if (!tagRuleModal) {
        tagRuleModal = new bootstrap.Modal(document.getElementById('tagRuleModal'));
    }
    tagRuleModal.show();
}

/**
 * Save tag rule
 */
async function saveTagRule() {
    const saveBtn = document.getElementById('save_rule_btn');
    const saveSpinner = document.getElementById('save_rule_spinner');
    const saveText = document.getElementById('save_rule_text');
    const errorEl = document.getElementById('rule_error');

    const ruleId = document.getElementById('rule_id').value;
    const tagId = parseInt(document.getElementById('rule_tag_id').value);
    const criterionType = document.getElementById('rule_criterion_type').value;
    const operator = document.getElementById('rule_operator').value;
    const value = document.getElementById('rule_value').value.trim();
    const priority = parseInt(document.getElementById('rule_priority').value);
    const enabled = document.getElementById('rule_enabled').checked;

    // Validate
    if (!tagId || !criterionType || !operator || !value) {
        showError(errorEl, 'All fields are required');
        return;
    }

    // Show loading state
    saveBtn.disabled = true;
    saveSpinner.classList.remove('d-none');
    saveText.textContent = ruleId ? 'Updating...' : 'Creating...';
    errorEl.classList.add('d-none');

    try {
        let response;
        const payload = {
            tag_id: tagId,
            criterion_type: criterionType,
            operator: operator,
            value: value,
            priority: priority,
            enabled: enabled
        };

        if (ruleId) {
            // Update existing rule
            response = await authenticatedFetch(`/api/v1/tag-rules/${ruleId}`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });
        } else {
            // Create new rule
            response = await authenticatedFetch('/api/v1/tag-rules', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });
        }

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || `Failed to save rule: ${response.statusText}`);
        }

        // Success - reload rules and close modal
        await loadTagRules();
        tagRuleModal.hide();

        // Reset form
        document.getElementById('rule_form').reset();

    } catch (error) {
        console.error('Error saving rule:', error);
        showError(errorEl, error.message);
    } finally {
        saveBtn.disabled = false;
        saveSpinner.classList.add('d-none');
        saveText.textContent = 'Save Rule';
    }
}

/**
 * Edit tag rule
 */
function editTagRule(ruleId) {
    openTagRuleModal(ruleId);
}

/**
 * Delete tag rule
 */
function deleteTagRule(ruleId) {
    currentEditingRuleId = ruleId;
    if (!deleteRuleModal) {
        deleteRuleModal = new bootstrap.Modal(document.getElementById('deleteRuleModal'));
    }
    deleteRuleModal.show();
}

/**
 * Confirm delete tag rule
 */
async function confirmDeleteTagRule() {
    const deleteBtn = document.getElementById('confirm_delete_rule_btn');
    const deleteSpinner = document.getElementById('delete_rule_spinner');
    const deleteText = document.getElementById('delete_rule_text');

    if (!currentEditingRuleId) {
        console.error('No rule selected for deletion');
        return;
    }

    // Show loading state
    deleteBtn.disabled = true;
    deleteSpinner.classList.remove('d-none');
    deleteText.textContent = 'Deleting...';

    try {
        const response = await authenticatedFetch(`/api/v1/tag-rules/${currentEditingRuleId}`, {
            method: 'DELETE'
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || `Failed to delete rule: ${response.statusText}`);
        }

        // Success - reload rules and close modal
        await loadTagRules();
        deleteRuleModal.hide();

    } catch (error) {
        console.error('Error deleting rule:', error);
        alert(`Failed to delete rule: ${error.message}`);
    } finally {
        deleteBtn.disabled = false;
        deleteSpinner.classList.add('d-none');
        deleteText.textContent = 'Delete Rule';
        currentEditingRuleId = null;
    }
}

/**
 * Apply all tag rules to existing files
 */
async function applyAllTagRules() {
    const applyBtn = document.getElementById('apply_rules_btn');
    const originalHtml = applyBtn.innerHTML;

    if (!confirm('Apply all enabled tag rules to all files in the inventory? This may take a few moments.')) {
        return;
    }

    // Show loading state
    applyBtn.disabled = true;
    applyBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status"></span> Applying...';

    try {
        const response = await authenticatedFetch('/api/v1/tag-rules/apply', {
            method: 'POST'
        });

        if (!response.ok) {
            throw new Error(`Failed to apply rules: ${response.statusText}`);
        }

        const result = await response.json();
        alert(`Rules applied successfully!\n\nFiles processed: ${result.files_processed}\nTags added: ${result.tags_added}`);

    } catch (error) {
        console.error('Error applying rules:', error);
        alert(`Failed to apply rules: ${error.message}`);
    } finally {
        applyBtn.disabled = false;
        applyBtn.innerHTML = originalHtml;
    }
}

// Set up tag rule event listeners when DOM is loaded
document.addEventListener('DOMContentLoaded', async () => {
    // Create rule button
    const createRuleBtn = document.getElementById('create_rule_btn');
    if (createRuleBtn) {
        createRuleBtn.addEventListener('click', () => openTagRuleModal());
    }

    // Save rule button
    const saveRuleBtn = document.getElementById('save_rule_btn');
    if (saveRuleBtn) {
        saveRuleBtn.addEventListener('click', saveTagRule);
    }

    // Confirm delete rule button
    const confirmDeleteRuleBtn = document.getElementById('confirm_delete_rule_btn');
    if (confirmDeleteRuleBtn) {
        confirmDeleteRuleBtn.addEventListener('click', confirmDeleteTagRule);
    }

    // Apply rules button
    const applyRulesBtn = document.getElementById('apply_rules_btn');
    if (applyRulesBtn) {
        applyRulesBtn.addEventListener('click', applyAllTagRules);
    }

    // Load tag rules
    await loadTagRules();
});
