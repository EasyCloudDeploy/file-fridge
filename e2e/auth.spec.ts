import { test, expect } from '@playwright/test';
import { getCredentials, collectBrowserFailures } from './helpers';

test.describe('Authentication Flow', () => {
    test('User can login and logout successfully', async ({ page }) => {
        const { username, password } = getCredentials();
        const browserFailures = collectBrowserFailures(page);

        // 1. Navigate to login page
        await page.goto('/login');
        await expect(page).toHaveTitle(/Login/i);

        // 2. Perform login
        await page.locator('#username').fill(username);
        await page.locator('#password').fill(password);
        
        await page.locator('#submit-btn').click();

        // 3. Verify dashboard loads
        await Promise.race([
            page.waitForURL(/\/$/),
            page.waitForSelector('#error-message:not(.d-none)').then(async () => {
                const errorText = await page.locator('#error-text').textContent();
                throw new Error(`Login failed with error: ${errorText} (used ${username}/${password})`);
            })
        ]);
        
        await expect(page.locator('#app-shell')).toBeVisible();
        await expect(page.locator('.app-sidebar')).toBeVisible();

        // 4. Verify token in session storage
        const token = await page.evaluate(() => window.sessionStorage.getItem('auth_token'));
        expect(token).toBeTruthy();

        // 5. Perform logout
        await page.getByRole('button', { name: /logout/i }).click();

        // 6. Verify redirection to login
        await page.waitForURL('**/login');
        const clearedToken = await page.evaluate(() => window.sessionStorage.getItem('auth_token'));
        expect(clearedToken).toBeNull();

        expect(browserFailures.failures).toEqual([]);
    });

    test('Protected routes redirect to login when unauthenticated', async ({ page }) => {
        const protectedRoutes = ['/', '/paths', '/files', '/settings', '/storage-locations'];

        for (const route of protectedRoutes) {
            await page.goto(route);
            await page.waitForURL('**/login');
            await expect(page.getByLabel(/username/i)).toBeVisible();
        }
    });

    test('Invalid login credentials show error message', async ({ page }) => {
        await page.goto('/login');
        await page.getByLabel(/username/i).fill('wronguser');
        await page.getByLabel(/password/i).fill('wrongpass');
        await page.getByRole('button', { name: /login/i }).click();

        // Assuming there is an alert or error message shown
        await expect(page.locator('.alert-danger, .error-message')).toBeVisible();
    });
});
