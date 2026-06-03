import { test, expect } from '@playwright/test';
import { loginViaUi, collectBrowserFailures } from './helpers';

test.describe('Notifiers Management', () => {
    const notifierName = `Test Notifier ${Date.now()}`;
    const webhookUrl = 'https://webhook.site/test';

    test.beforeEach(async ({ page }) => {
        await loginViaUi(page);
    });

    test('User can create, edit, and delete a webhook notifier', async ({ page }) => {
        const browserFailures = collectBrowserFailures(page);
        page.on('console', msg => console.log('BROWSER_CONSOLE:', msg.type(), msg.text()));
        page.on('pageerror', err => console.error('BROWSER_PAGEERROR:', err.message, err.stack));

        // 1. Navigate to Notifiers
        await page.goto('/notifiers');
        await expect(page).toHaveURL(/\/notifiers/);

        // 2. Create a new notifier
        await page.locator('#create_notifier_btn').click();
        await page.locator('#notifier_name').fill(notifierName);
        await page.locator('#notifier_type').selectOption('generic_webhook');
        await page.locator('#notifier_address').fill(webhookUrl);
        
        // Select all events
        await page.locator('#select_all_events').click();
        
        await page.locator('#save_notifier_btn').click();
        await expect(page.locator('#notifier_modal')).not.toBeVisible();

        // 3. Verify it appears in the list
        await expect(page.locator('#notifiers_table_body')).toContainText(notifierName, { timeout: 15000 });

        // 4. Edit the notifier
        const row = page.locator('#notifiers_table_body tr', { hasText: notifierName });
        await row.getByTitle('Edit').click();
        
        const newNotifierName = `${notifierName} Edited`;
        await page.locator('#notifier_name').fill(newNotifierName);
        await page.locator('#save_notifier_btn').click();
        await expect(page.locator('#notifier_modal')).not.toBeVisible();

        // 5. Verify update
        await expect(page.locator('#notifiers_table_body')).toContainText(newNotifierName, { timeout: 15000 });

        // 6. Delete the notifier
        const updatedRow = page.locator('#notifiers_table_body tr', { hasText: newNotifierName });
        await updatedRow.getByTitle('Delete').click();
        
        // Confirm deletion
        const confirmButton = page.locator('#confirm_delete_btn');
        await expect(confirmButton).toBeVisible();
        await confirmButton.click();

        // 7. Verify it's gone
        await expect(page.locator('#notifiers_table_body')).not.toContainText(newNotifierName, { timeout: 15000 });

        const filteredFailures = browserFailures.failures.filter(
            (entry) => !entry.includes('/api/v1/remote/connections')
        );
        expect(filteredFailures).toEqual([]);
    });
});
