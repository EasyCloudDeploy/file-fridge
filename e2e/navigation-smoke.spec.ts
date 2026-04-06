import { test, expect } from '@playwright/test';
import { fastLogin, apiLogin } from './helpers';
import * as fs from 'fs';
import * as path from 'path';

test.describe('Navigation Smoke Test', () => {
    test.beforeEach(async ({ page, request }) => {
        await fastLogin(page, request);
    });

    const routes = [
        { path: '/', title: /Dashboard/i },
        { path: '/files', title: /Files|Inventory/i },
        { path: '/paths', title: /Paths/i },
        { path: '/paths/new', title: /Path/i },
        { path: '/stats', title: /Statistics|Stats/i },
        { path: '/storage-locations', title: /Storage/i },
        { path: '/storage-locations/new', title: /Storage/i },
        { path: '/tags', title: /Tags/i },
        { path: '/migrations', title: /Migrations/i },
        { path: '/notifiers', title: /Notifiers/i },
        { path: '/settings', title: /Settings/i },
    ];

    for (const route of routes) {
        test(`User can navigate to ${route.path}`, async ({ page }) => {
            await page.goto(route.path);
            await expect(page).toHaveTitle(route.title);
            // Ensure sidebar is visible on all these pages
            await expect(page.locator('.app-sidebar')).toBeVisible();
        });
    }
});

test.describe('Dynamic Routes Smoke Test', () => {
    let pathId: number;
    let locationId: number;

    test.beforeAll(async ({ request }) => {
        const token = await apiLogin(request);
        const hotPath = path.join(process.cwd(), 'data', `smoke-hot-${Date.now()}`);
        const coldPath = path.join(process.cwd(), 'data', `smoke-cold-${Date.now()}`);

        if (!fs.existsSync(hotPath)) fs.mkdirSync(hotPath, { recursive: true });
        if (!fs.existsSync(coldPath)) fs.mkdirSync(coldPath, { recursive: true });

        // Create a storage location
        const storageResponse = await request.post('/api/v1/storage/locations', {
            headers: { Authorization: `Bearer ${token}` },
            data: {
                name: `Smoke Test Storage ${Date.now()}`,
                path: coldPath,
                caution_threshold_percent: 20,
                critical_threshold_percent: 10,
                is_encrypted: false
            }
        });
        const storageData = await storageResponse.json();
        locationId = storageData.id;

        // Create a monitored path
        const pathResponse = await request.post('/api/v1/paths', {
            headers: { Authorization: `Bearer ${token}` },
            data: {
                name: `Smoke Test Path ${Date.now()}`,
                source_path: hotPath,
                storage_location_ids: [locationId],
                check_interval_seconds: 3600
            }
        });
        const pathData = await pathResponse.json();
        if (!pathResponse.ok()) {
            console.error('Failed to create path:', pathData);
        }
        expect(pathResponse.ok()).toBeTruthy();
        pathId = pathData.id;
    });

    test.beforeEach(async ({ page, request }) => {
        await fastLogin(page, request);
    });

    test('User can navigate to path details', async ({ page }) => {
        await page.goto(`/paths/${pathId}`);
        await expect(page.locator('.page-frame__title')).toBeVisible();
    });

    test('User can navigate to edit path', async ({ page }) => {
        await page.goto(`/paths/${pathId}/edit`);
        await expect(page.locator('.page-frame__title')).toContainText(/Path/i);
    });

    test('User can navigate to storage location details', async ({ page }) => {
        await page.goto(`/storage-locations/${locationId}`);
        await expect(page.locator('.page-frame__title')).toBeVisible();
    });

    test('User can navigate to edit storage location', async ({ page }) => {
        await page.goto(`/storage-locations/${locationId}/edit`);
        await expect(page.locator('.page-frame__title')).toContainText(/Storage/i);
    });
});
