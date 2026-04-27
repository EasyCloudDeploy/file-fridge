import { test, expect } from '@playwright/test';
import { loginViaUi, collectBrowserFailures } from './helpers';

test.describe('Settings and Security', () => {
    test.beforeEach(async ({ page, context }) => {
        await context.grantPermissions(['clipboard-read', 'clipboard-write']);
        await loginViaUi(page);
    });

    test('User can generate and copy an API token', async ({ page }) => {
        const browserFailures = collectBrowserFailures(page);

        await page.goto('/settings');
        
        // API Tokens are in the first section (Accounts)
        await page.locator('#generate-token-btn').click();
        
        // Result should appear
        const tokenResult = page.locator('#token-result');
        await expect(tokenResult).toBeVisible();
        await expect(page.locator('#generated-token')).not.toHaveValue('');
        
        // Copy button
        await page.locator('#copy-token-btn').click();
        // Toast notification (use .toast-body specifically to avoid confusion with other alerts)
        await expect(page.locator('.toast-body')).toContainText(/copied/i);

        const filteredFailures = browserFailures.failures.filter(
            (entry) => !entry.includes('/api/v1/remote/connections')
        );
        expect(filteredFailures).toEqual([]);
    });

    test('P2P Network settings section is accessible', async ({ page }) => {
        const browserFailures = collectBrowserFailures(page);

        await page.goto('/settings');
        
        // Click on Remote Connections in nav
        await page.locator('#nav-remote-connections').click();
        
        // Check P2P Network card heading
        await expect(page.getByRole('heading', { name: /P2P Network/i })).toBeVisible();
        await expect(page.locator('#p2p-network-name')).toBeVisible();

        const filteredFailures = browserFailures.failures.filter(
            (entry) => !entry.includes('/api/v1/remote/connections')
        );
        expect(filteredFailures).toEqual([]);
    });
});
