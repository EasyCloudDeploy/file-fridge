import { expect, type APIRequestContext, type Page } from '@playwright/test';

const username = process.env.FILE_FRIDGE_E2E_USERNAME || 'admin';
const password = process.env.FILE_FRIDGE_E2E_PASSWORD || 'secret123';
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
    await page.locator('#username').fill(credentials.username);
    await page.locator('#password').fill(credentials.password);
    
    await page.locator('#submit-btn').click();
    
    // Wait for either success (redirection) or error message
    await Promise.race([
        page.waitForURL(/\/$/),
        page.waitForSelector('#error-message:not(.d-none)'),
    ]);

    const errorVisible = await page.locator('#error-message').isVisible();
    if (errorVisible) {
        const errorText = await page.locator('#error-text').textContent();
        throw new Error(`Login failed via UI: ${errorText}`);
    }

    await expect(page.locator('#app-shell')).toBeVisible();
}

/**
 * Fast login by obtaining a token via API and seeding it into sessionStorage.
 */
export async function fastLogin(page: Page, request: APIRequestContext): Promise<void> {
    const token = await apiLogin(request);
    await seedAuthToken(page, token);
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
