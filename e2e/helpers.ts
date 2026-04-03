import { expect, type APIRequestContext, type Page } from '@playwright/test';

const username = process.env.FILE_FRIDGE_E2E_USERNAME;
const password = process.env.FILE_FRIDGE_E2E_PASSWORD;
let cachedToken: string | null = null;

export function getCredentials(): { username: string; password: string } {
    if (!username || !password) {
        throw new Error(
            'Missing FILE_FRIDGE_E2E_USERNAME or FILE_FRIDGE_E2E_PASSWORD environment variable.'
        );
    }

    return { username, password };
}

export async function loginViaUi(page: Page): Promise<void> {
    const credentials = getCredentials();

    await page.goto('/login');
    await page.getByLabel(/username/i).fill(credentials.username);
    await page.getByLabel(/password/i).fill(credentials.password);
    await page.getByRole('button', { name: /login|create account/i }).click();
    await page.waitForURL('**/');
    await expect(page.locator('#app-shell')).toBeVisible();
}

export async function apiLogin(request: APIRequestContext): Promise<string> {
    if (cachedToken) {
        return cachedToken;
    }

    const credentials = getCredentials();
    const response = await request.post('/api/v1/auth/login', {
        data: credentials,
    });

    expect(response.ok()).toBeTruthy();

    const data = await response.json();
    cachedToken = data.access_token as string;
    return cachedToken;
}

export async function seedAuthToken(page: Page, token: string): Promise<void> {
    await page.addInitScript((jwt: string) => {
        window.sessionStorage.setItem('auth_token', jwt);
    }, token);
}

export function collectBrowserFailures(page: Page): { failures: string[] } {
    const failures: string[] = [];

    page.on('pageerror', (error) => {
        failures.push(`pageerror: ${error.message}`);
    });

    page.on('console', (message) => {
        if (message.type() === 'error') {
            failures.push(`console:${message.type()}: ${message.text()}`);
        }
    });

    return { failures };
}

export async function expectNoHorizontalOverflow(page: Page): Promise<void> {
    const overflow = await page.evaluate(() => {
        return document.documentElement.scrollWidth - window.innerWidth;
    });

    expect(overflow).toBeLessThanOrEqual(1);
}

export async function waitForAnyVisible(page: Page, selectors: string[]): Promise<void> {
    if (selectors.length === 0) {
        return;
    }

    await page.waitForFunction(
        (activeSelectors) => {
            return activeSelectors.some((selector) => {
                const element = document.querySelector(selector);
                if (!element) {
                    return false;
                }

                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return (
                    style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && rect.width > 0
                    && rect.height > 0
                );
            });
        },
        selectors,
        { timeout: 15_000 }
    );
}

export async function assertScriptsPresent(page: Page, scripts: string[]): Promise<void> {
    for (const script of scripts) {
        await expect(page.locator(`script[src="${script}"]`)).toHaveCount(1);
    }
}

export async function assertLoadingResolved(page: Page, selectors: string[]): Promise<void> {
    for (const selector of selectors) {
        const locator = page.locator(selector);
        const count = await locator.count();
        if (count === 0) {
            continue;
        }

        await expect(locator.first()).toBeHidden({ timeout: 15_000 });
    }
}
