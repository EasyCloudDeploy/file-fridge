import { test, expect } from '@playwright/test';

import { routeContracts, type RouteContext } from './route-contracts';
import {
    apiLogin,
    assertLoadingResolved,
    assertScriptsPresent,
    collectBrowserFailures,
    expectNoHorizontalOverflow,
    loginViaUi,
    seedAuthToken,
    waitForAnyVisible,
} from './helpers';

const baseURL = process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:8000';
const baseOrigin = new URL(baseURL).origin;

async function buildRouteContext(request: Parameters<typeof apiLogin>[0], token: string): Promise<RouteContext> {
    const headers = { Authorization: `Bearer ${token}` };

    const pathsResponse = await request.get('/api/v1/paths', { headers });
    expect(pathsResponse.ok()).toBeTruthy();
    const paths = (await pathsResponse.json()) as Array<{ id: number }>;

    return {
        primaryPathId: paths.length > 0 ? paths[0].id : null,
    };
}

function resolvePath(
    contractPath: string | ((context: RouteContext) => string | null),
    context: RouteContext
): string | null {
    return typeof contractPath === 'function' ? contractPath(context) : contractPath;
}

test.describe('authentication smoke', () => {
    test('login stores the token and logout returns to /login', async ({ page }) => {
        const browserFailures = collectBrowserFailures(page);

        await loginViaUi(page);

        const token = await page.evaluate(() => window.sessionStorage.getItem('auth_token'));
        expect(token).toBeTruthy();
        await expect(page).toHaveURL(/\/$/);

        await page.getByRole('button', { name: /logout/i }).click();
        await page.waitForURL('**/login');

        const clearedToken = await page.evaluate(() => window.sessionStorage.getItem('auth_token'));
        expect(clearedToken).toBeNull();
        const filteredFailures = browserFailures.failures.filter(
            (entry) =>
                !entry.includes('401 (Unauthorized)')
                && !entry.includes('Authentication required')
        );
        expect(filteredFailures).toEqual([]);
    });

    test('protected routes redirect to /login when the auth token is missing', async ({ page }) => {
        const browserFailures = collectBrowserFailures(page);

        await page.goto('/paths');
        await page.waitForURL('**/login');

        const realFailures = browserFailures.failures.filter(f => !f.includes('TypeError: Failed to fetch'));
        expect(realFailures).toEqual([]);
    });
});

test.describe('authenticated route smoke', () => {
    let token: string;
    let routeContext: RouteContext;

    test.beforeAll(async ({ request }) => {
        token = await apiLogin(request);
        routeContext = await buildRouteContext(request, token);
    });

    for (const contract of routeContracts) {
        test(`${contract.name} loads with its declared API contract`, async ({ page }) => {
            const resolvedPath = resolvePath(contract.path, routeContext);
            test.skip(!resolvedPath, `No local data available for ${contract.name}`);

            const browserFailures = collectBrowserFailures(page);
            const externalRequests: string[] = [];
            const seenApiPaths = new Set<string>();

            page.on('request', (request) => {
                const url = request.url();
                if (url.startsWith('data:') || url.startsWith('blob:') || url.startsWith('about:')) {
                    return;
                }

                if (url.startsWith('http') && !url.startsWith(baseOrigin)) {
                    externalRequests.push(url);
                }
            });

            page.on('response', (response) => {
                const url = response.url();
                if (!url.startsWith(baseOrigin)) {
                    return;
                }

                const pathname = new URL(url).pathname;
                if (pathname.startsWith('/api/v1/') || pathname === '/health') {
                    seenApiPaths.add(pathname);
                }
            });

            await seedAuthToken(page, token);
            await page.goto(resolvedPath!, { waitUntil: 'domcontentloaded' });
            await waitForAnyVisible(page, contract.readySelectors);
            await assertScriptsPresent(page, contract.scripts);
            await assertLoadingResolved(page, contract.loadingSelectors);
            await expect(page.locator('#app-version')).not.toHaveText(/Loading/i);
            await expectNoHorizontalOverflow(page);

            for (const expectedApi of contract.expectedApis) {
                expect(
                    [...seenApiPaths].some((path) => path.includes(expectedApi)),
                    `${contract.name} did not call ${expectedApi}. Saw: ${[...seenApiPaths].join(', ')}`
                ).toBeTruthy();
            }

            expect(externalRequests).toEqual([]);
            const filteredFailures = browserFailures.failures.filter((entry) => {
                if (contract.name !== 'settings') {
                    return true;
                }
                return !entry.includes('404 (Not Found)');
            });
            expect(filteredFailures).toEqual([]);
        });
    }
});

test.describe('responsive layouts', () => {
    let token: string;

    const responsiveRoutes = ['/', '/paths', '/storage-locations', '/files', '/tags', '/settings'];
    const viewports = [
        { name: 'desktop', width: 1440, height: 1100 },
        { name: 'tablet', width: 1024, height: 900 },
        { name: 'mobile', width: 390, height: 844 },
    ];

    test.beforeAll(async ({ request }) => {
        token = await apiLogin(request);
    });

    for (const viewport of viewports) {
        for (const route of responsiveRoutes) {
            test(`${route} stays readable at ${viewport.name}`, async ({ page }) => {
                const browserFailures = collectBrowserFailures(page);

                await page.setViewportSize({ width: viewport.width, height: viewport.height });
                await seedAuthToken(page, token);
                await page.goto(route, { waitUntil: 'domcontentloaded' });

                await expect(page.locator('.app-sidebar')).toBeVisible();
                await expect(page.locator('.page-frame__title')).toBeVisible();
                await expectNoHorizontalOverflow(page);
                const filteredFailures = browserFailures.failures.filter((entry) => {
                    if (route !== '/settings') {
                        return true;
                    }
                    return !entry.includes('404 (Not Found)');
                });
                expect(filteredFailures).toEqual([]);
            });
        }
    }
});
