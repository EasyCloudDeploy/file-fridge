import { test, expect } from '@playwright/test';
import { loginViaUi, collectBrowserFailures } from './helpers';

test.describe('Files View and AG Grid', () => {
    test.beforeEach(async ({ page }) => {
        await loginViaUi(page);
    });

    test('User can browse, filter, and pin files', async ({ page }) => {
        const browserFailures = collectBrowserFailures(page);

        await page.goto('/files');
        
        // 1. Check if grid or empty state is loaded
        const grid = page.locator('#filesGrid');
        const emptyState = page.locator('#no-files-message');
        
        // Wait for loading to finish
        await expect(page.locator('#files-loading')).not.toBeVisible({ timeout: 20000 });
        
        // Either grid or empty state should be visible
        const isGridVisible = await grid.isVisible();
        if (!isGridVisible) {
            await expect(emptyState).toBeVisible();
            console.log('No files found, skipping pinning test.');
        }

        // 2. Test Search filter (if grid is visible)
        if (isGridVisible) {
            const searchInput = page.locator('#search_input');
            await searchInput.fill('test');
            await page.keyboard.press('Enter');
            
            // 3. Test pinning a file using bulk actions
            // Find first row checkbox
            const firstRow = page.locator('.ag-row').first();
            if (await firstRow.isVisible()) {
                // Select the row
                await firstRow.locator('.ag-selection-checkbox').click();
                
                // Wait for bulk toolbar
                const bulkToolbar = page.locator('#bulk-actions-toolbar');
                await expect(bulkToolbar).toBeVisible();
                
                // Toggle pin (click pin if not pinned, unpin if pinned)
                const isPinned = await firstRow.locator('.bi-pin-fill').isVisible();
                if (isPinned) {
                    await page.locator('#bulk-unpin-btn').click();
                    await expect(firstRow.locator('.bi-pin-fill')).toBeHidden();
                } else {
                    await page.locator('#bulk-pin-btn').click();
                    await expect(firstRow.locator('.bi-pin-fill')).toBeVisible();
                }
            }
        }

        const filteredFailures = browserFailures.failures.filter(
            (entry) => !entry.includes('/api/v1/remote/connections')
        );
        expect(filteredFailures).toEqual([]);
    });
});
