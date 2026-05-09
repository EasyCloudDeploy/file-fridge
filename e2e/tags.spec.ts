import { test, expect } from '@playwright/test';
import { loginViaUi, apiLogin, collectBrowserFailures } from './helpers';

test.describe('Tags Management', () => {
    const tagName = `Test Tag ${Date.now()}`;
    const tagColor = '#ff5733';

    test.beforeEach(async ({ page }) => {
        await loginViaUi(page);
    });

    test('User can create, edit, and delete a tag', async ({ page }) => {
        const browserFailures = collectBrowserFailures(page);

        // 1. Navigate to Tags
        await page.goto('/tags');
        await expect(page).toHaveURL(/\/tags/);

        // 2. Create a new tag
        await page.locator('#create_tag_btn').click();
        await page.locator('#tag_name').fill(tagName);
        await page.locator('#tag_color').fill(tagColor);
        await page.locator('#save_tag_btn').click();

        // 3. Verify it appears in the list
        await expect(page.locator('#tags_table_body')).toContainText(tagName, { timeout: 15000 });

        // 4. Edit the tag
        const row = page.locator('#tags_table_body tr', { hasText: tagName });
        await row.getByTitle('Edit tag').click();
        
        const newTagName = `${tagName} Edited`;
        await page.locator('#tag_name').fill(newTagName);
        await page.locator('#save_tag_btn').click();

        // 5. Verify update
        await expect(page.locator('#tags_table_body')).toContainText(newTagName, { timeout: 15000 });

        // 6. Delete the tag
        const updatedRow = page.locator('#tags_table_body tr', { hasText: newTagName });
        await updatedRow.getByTitle('Delete tag').click();
        
        // Confirm deletion
        const confirmButton = page.locator('#confirm_delete_tag_btn');
        await expect(confirmButton).toBeVisible();
        await confirmButton.click();

        // 7. Verify it's gone
        await expect(page.locator('#tags_table_body')).not.toContainText(newTagName, { timeout: 15000 });

        const filteredFailures = browserFailures.failures.filter(
            (entry) => !entry.includes('/api/v1/remote/connections') // Ignore legacy errors for now
        );
        expect(filteredFailures).toEqual([]);
    });
});
