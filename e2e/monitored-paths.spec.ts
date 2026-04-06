import { test, expect } from '@playwright/test';
import { loginViaUi, apiLogin, collectBrowserFailures } from './helpers';
import * as fs from 'fs';
import * as path from 'path';

test.describe('Monitored Paths', () => {
    const pathName = `Test Path ${Date.now()}`;
    const storageName = `Storage for Path Test ${Date.now()}`;
    const hotPath = path.join(process.cwd(), 'data', `test-hot-${Date.now()}`);
    const coldPath = path.join(process.cwd(), 'data', `test-cold-${Date.now()}`);

    test.beforeAll(async ({ request }) => {
        // Ensure test directories exist
        if (!fs.existsSync(hotPath)) fs.mkdirSync(hotPath, { recursive: true });
        if (!fs.existsSync(coldPath)) fs.mkdirSync(coldPath, { recursive: true });

        // Create a storage location via API for testing efficiency
        const token = await apiLogin(request);
        const response = await request.post('/api/v1/storage/locations', {
            headers: { Authorization: `Bearer ${token}` },
            data: {
                name: storageName,
                path: coldPath,
                caution_threshold_percent: 20,
                critical_threshold_percent: 10,
                is_encrypted: false
            }
        });
        expect(response.ok()).toBeTruthy();
    });

    test.beforeEach(async ({ page }) => {
        await loginViaUi(page);
    });

    test('User can create and delete a monitored path', async ({ page }) => {
        const browserFailures = collectBrowserFailures(page);

        // 1. Navigate to monitored paths
        await page.goto('/paths');
        await page.getByRole('link', { name: /add path/i }).first().click();

        // 2. Fill out the form
        await page.getByLabel(/name \*/i).fill(pathName);
        await page.getByLabel(/source path \*/i).fill(hotPath);

        // 3. Select the storage location (checkbox)
        // Wait for checkboxes to be loaded by JavaScript
        await page.waitForSelector('#storage-locations-container input[type="checkbox"]');
        await page.getByLabel(storageName).check();

        // 4. Submit form
        await page.getByRole('button', { name: /create path/i }).click();

        // 5. Verify it's in the list or detail page
        await page.waitForURL(url => url.pathname === '/paths' || url.pathname.match(/\/paths\/\d+$/) !== null);

        // If on detail page, go back to list
        if (page.url().includes('/paths/')) {
            await page.goto('/paths');
        }

        await expect(page.getByText(pathName)).toBeVisible();

        // 6. Delete the path
        // Wait for table to be populated
        await page.waitForSelector('#pathsTable tbody tr');
        
        const row = page.locator('tr', { hasText: pathName });
        await row.getByRole('button', { name: /delete/i }).click();

        // Handle confirmation
        page.once('dialog', dialog => dialog.accept());
        const confirmButton = page.getByRole('button', { name: /confirm|delete/i }).filter({ hasText: /delete/i });
        if (await confirmButton.isVisible()) {
            await confirmButton.click();
        }

        // 7. Verify it's gone
        await expect(page.getByText(pathName)).not.toBeVisible();

        expect(browserFailures.failures).toEqual([]);
    });
});
