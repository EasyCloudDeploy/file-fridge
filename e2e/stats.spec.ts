import { test, expect } from '@playwright/test';
import { loginViaUi, collectBrowserFailures } from './helpers';

test.describe('Statistics and Dashboard', () => {
    test.beforeEach(async ({ page }) => {
        await loginViaUi(page);
    });

    test('Dashboard displays storage status and recent activity', async ({ page }) => {
        const browserFailures = collectBrowserFailures(page);

        await page.goto('/');
        
        // Check stat tiles
        await expect(page.locator('#totalFiles')).toBeVisible();
        await expect(page.locator('#totalSize')).toBeVisible();
        
        // Check recent activity table
        await expect(page.locator('#recentFilesList')).toBeVisible();
        
        // Verify no unexpected browser errors
        const filteredFailures = browserFailures.failures.filter(
            (entry) => !entry.includes('/api/v1/remote/connections') && !entry.includes('Failed to fetch')
        );
        expect(filteredFailures).toEqual([]);
    });

    test('Statistics page displays charts and metrics', async ({ page }) => {
        const browserFailures = collectBrowserFailures(page);

        await page.goto('/stats');
        
        // Check overview cards
        await expect(page.locator('#totalFiles')).toBeVisible();
        await expect(page.locator('#totalSize')).toBeVisible();
        await expect(page.locator('#hotFiles')).toBeVisible();
        await expect(page.locator('#coldFiles')).toBeVisible();
        
        // Check performance metrics
        await expect(page.locator('#files24h')).toBeVisible();
        await expect(page.locator('#avgPerDay')).toBeVisible();
        
        // Check charts are rendered (canvases should be visible after loading)
        // We wait for the loading spinner to disappear
        await expect(page.locator('#daily-chart-loading')).not.toBeVisible({ timeout: 10000 });
        await expect(page.locator('#dailyChart')).toBeVisible();
        
        await expect(page.locator('#storage-chart-loading')).not.toBeVisible();
        await expect(page.locator('#storageChart')).toBeVisible();

        const filteredFailures = browserFailures.failures.filter(
            (entry) => !entry.includes('/api/v1/remote/connections') && !entry.includes('Failed to fetch')
        );
        expect(filteredFailures).toEqual([]);
    });
});
