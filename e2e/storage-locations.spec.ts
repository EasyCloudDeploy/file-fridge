import { test, expect } from '@playwright/test';
import { loginViaUi, collectBrowserFailures } from './helpers';
import * as fs from 'fs';
import * as path from 'path';

test.describe('Storage Locations', () => {
    test.beforeEach(async ({ page }) => {
        await loginViaUi(page);
    });

    test('User can create and delete a storage location', async ({ page }) => {
        const browserFailures = collectBrowserFailures(page);
        const locationName = `Test Storage ${Date.now()}`;
        const storagePath = path.join(process.cwd(), 'data', `test-storage-${Date.now()}`);

        // Ensure the storage path exists
        if (!fs.existsSync(storagePath)) {
            fs.mkdirSync(storagePath, { recursive: true });
        }

        // 1. Navigate to storage locations
        await page.goto('/storage-locations');
        await page.getByRole('link', { name: /add location/i }).first().click();

        // 2. Fill out the form
        await page.getByLabel(/name \*/i).fill(locationName);
        await page.getByLabel(/path \*/i).fill(storagePath);
        await page.getByRole('button', { name: /create location/i }).click();

        // 3. Verify it's in the list
        await page.waitForURL('**/storage-locations');
        await expect(page.getByText(locationName)).toBeVisible();

        // 4. Delete the location
        // Find the row with the location name and click delete
        const row = page.locator('tr', { hasText: locationName });
        await row.getByRole('button', { name: /delete/i }).click();

        // Handle confirmation dialog if present (assuming a browser confirm or a modal)
        // If it's a browser confirm:
        page.once('dialog', dialog => dialog.accept());
        // 5. Confirm deletion in modal
        const confirmButton = page.locator('#confirm-delete-button');
        if (await confirmButton.isVisible()) {
            await confirmButton.click();
        }

        // Wait for modal to hide
        await expect(page.locator('#deleteLocationModal')).toBeHidden();

        // 6. Verify it's gone from the table
        await expect(page.locator('table#locationsTable')).not.toContainText(locationName);

        expect(browserFailures.failures).toEqual([]);
    });
});
