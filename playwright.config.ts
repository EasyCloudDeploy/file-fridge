import { defineConfig } from '@playwright/test';

const baseURL = process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:8000';

export default defineConfig({
    testDir: './e2e',
    fullyParallel: false,
    workers: 1,
    timeout: 60_000,
    expect: {
        timeout: 10_000,
    },
    use: {
        baseURL,
        headless: true,
        trace: 'retain-on-failure',
        screenshot: 'only-on-failure',
        video: 'retain-on-failure',
    },
    reporter: [['list'], ['html', { open: 'never' }]],
    webServer: {
        command: 'uv run uvicorn app.main:app --host 127.0.0.1 --port 8000',
        url: 'http://127.0.0.1:8000/health',
        reuseExistingServer: !process.env.CI,
        stdout: 'pipe',
        stderr: 'pipe',
        timeout: 120_000,
        env: {
            SECRET_KEY: 'dummy_key_for_e2e_testing',
            DATABASE_PATH: 'data/test_file_fridge.db',
            DISABLE_RATE_LIMIT: 'true',
            TESTING: 'true'
        },
    },
});
