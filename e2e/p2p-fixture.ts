import { request as playwrightRequest, type APIRequestContext } from '@playwright/test';
import { randomBytes } from 'crypto';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { spawn, type ChildProcessWithoutNullStreams } from 'child_process';
import net from 'net';

type FileFridgeInstance = {
    baseUrl: string;
    port: number;
    rootDir: string;
    hotDir: string;
    coldDir: string;
    databasePath: string;
    schedulerPath: string;
    stdout: string[];
    stderr: string[];
    process: ChildProcessWithoutNullStreams;
};

type UserToken = {
    username: string;
    password: string;
    token: string;
};

async function getFreePort(): Promise<number> {
    return await new Promise((resolve, reject) => {
        const server = net.createServer();
        server.listen(0, '127.0.0.1', () => {
            const address = server.address();
            if (!address || typeof address === 'string') {
                server.close();
                reject(new Error('Failed to resolve free port'));
                return;
            }
            const port = address.port;
            server.close(() => resolve(port));
        });
        server.on('error', reject);
    });
}

export function randomId(prefix: string): string {
    return `${prefix}-${Date.now()}-${randomBytes(4).toString('hex')}`;
}

export async function startFileFridgeInstance(name: string): Promise<FileFridgeInstance> {
    const port = await getFreePort();
    const rootDir = fs.mkdtempSync(path.join(os.tmpdir(), `file-fridge-${name}-`));
    const hotDir = path.join(rootDir, 'hot');
    const coldDir = path.join(rootDir, 'cold');
    const databasePath = path.join(rootDir, 'instance.db');
    const schedulerPath = path.join(rootDir, 'instance_scheduler.db');
    const stdout: string[] = [];
    const stderr: string[] = [];

    fs.mkdirSync(hotDir, { recursive: true });
    fs.mkdirSync(coldDir, { recursive: true });

    const localUvPath = path.join(process.cwd(), '.venv', 'bin', 'uv');
    const uvPath = fs.existsSync(localUvPath) ? localUvPath : 'uv';
    const appEnv = {
        ...process.env,
        SECRET_KEY: 'dummy_key_for_e2e_testing',
        DATABASE_PATH: databasePath,
        DISABLE_RATE_LIMIT: 'true',
        TESTING: 'true',
        LOG_LEVEL: 'INFO',
    };

    const child = spawn(
        uvPath,
        ['run', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)],
        {
            cwd: process.cwd(),
            env: appEnv,
            stdio: 'pipe',
        }
    );

    child.stdout.on('data', (chunk: Buffer) => stdout.push(chunk.toString()));
    child.stderr.on('data', (chunk: Buffer) => stderr.push(chunk.toString()));

    const baseUrl = `http://127.0.0.1:${port}`;
    await waitForHealth(baseUrl, 90_000);

    return {
        baseUrl,
        port,
        rootDir,
        hotDir,
        coldDir,
        databasePath,
        schedulerPath,
        stdout,
        stderr,
        process: child,
    };
}

export async function stopFileFridgeInstance(instance: FileFridgeInstance): Promise<void> {
    await new Promise<void>((resolve) => {
        if (instance.process.killed || instance.process.exitCode !== null) {
            resolve();
            return;
        }
        instance.process.once('exit', () => resolve());
        instance.process.kill('SIGTERM');
        setTimeout(() => {
            if (instance.process.exitCode === null) {
                instance.process.kill('SIGKILL');
            }
        }, 5_000);
    });

    try {
        fs.rmSync(instance.rootDir, { recursive: true, force: true });
    } catch {
        // Best-effort cleanup only.
    }
}

async function waitForHealth(baseUrl: string, timeoutMs: number): Promise<void> {
    const start = Date.now();
    let lastError = '';
    while (Date.now() - start < timeoutMs) {
        try {
            const response = await fetch(`${baseUrl}/health`);
            if (response.ok) {
                return;
            }
            lastError = `HTTP ${response.status}`;
        } catch (error) {
            lastError = String(error);
        }
        await sleep(500);
    }
    throw new Error(`Timed out waiting for health check on ${baseUrl}: ${lastError}`);
}

export async function bootstrapUserAndToken(baseUrl: string): Promise<UserToken> {
    const username = 'admin';
    const password = 'secret123';
    const api = await playwrightRequest.newContext({ baseURL: baseUrl });

    try {
        const check = await api.get('/api/v1/auth/check');
        if (!check.ok()) {
            throw new Error(`GET /auth/check failed: ${check.status()} ${await check.text()}`);
        }

        const authCheck = await check.json() as { setup_required: boolean };
        if (authCheck.setup_required) {
            const setup = await api.post('/api/v1/auth/setup', {
                data: { username, password },
            });
            if (!setup.ok()) {
                throw new Error(`POST /auth/setup failed: ${setup.status()} ${await setup.text()}`);
            }
            const setupToken = await setup.json() as { access_token: string };
            return { username, password, token: setupToken.access_token };
        }

        const login = await api.post('/api/v1/auth/login', {
            data: { username, password },
        });
        if (!login.ok()) {
            throw new Error(`POST /auth/login failed: ${login.status()} ${await login.text()}`);
        }
        const loginPayload = await login.json() as { access_token: string };
        return { username, password, token: loginPayload.access_token };
    } finally {
        await api.dispose();
    }
}

export async function createAuthedApi(baseUrl: string, token: string): Promise<APIRequestContext> {
    return await playwrightRequest.newContext({
        baseURL: baseUrl,
        extraHTTPHeaders: {
            Authorization: `Bearer ${token}`,
        },
    });
}

export async function createStorageAndPath(
    api: APIRequestContext,
    args: { storageName: string; coldPath: string; pathName: string; hotPath: string }
): Promise<{ storageId: number; pathId: number }> {
    const storageResp = await api.post('/api/v1/storage/locations', {
        data: {
            name: args.storageName,
            path: args.coldPath,
            caution_threshold_percent: 20,
            critical_threshold_percent: 10,
            is_encrypted: false,
        },
    });
    if (!storageResp.ok()) {
        throw new Error(`Create storage failed: ${storageResp.status()} ${await storageResp.text()}`);
    }
    const storage = await storageResp.json() as { id: number };

    const pathResp = await api.post('/api/v1/paths', {
        data: {
            name: args.pathName,
            source_path: args.hotPath,
            check_interval_seconds: 60,
            operation_type: 'move',
            enabled: true,
            prevent_indexing: false,
            max_concurrent_migrations: 1,
            storage_location_ids: [storage.id],
        },
    });
    if (!pathResp.ok()) {
        throw new Error(`Create path failed: ${pathResp.status()} ${await pathResp.text()}`);
    }
    const monitoredPath = await pathResp.json() as { id: number };

    return { storageId: storage.id, pathId: monitoredPath.id };
}

export async function triggerScan(api: APIRequestContext, pathId: number): Promise<void> {
    const scanResp = await api.post(`/api/v1/paths/${pathId}/scan`);
    if (!scanResp.ok() && scanResp.status() !== 409) {
        throw new Error(`Trigger scan failed: ${scanResp.status()} ${await scanResp.text()}`);
    }
}

type FileListRow = {
    id: number | string;
    file_path: string;
    display_file_path: string;
    is_remote: boolean;
    remote_peer_id: number | null;
};

export async function listFiles(api: APIRequestContext, query = ''): Promise<FileListRow[]> {
    const suffix = query ? `?${query}` : '';
    const response = await api.get(`/api/v1/files${suffix}`);
    if (!response.ok()) {
        throw new Error(`List files failed: ${response.status()} ${await response.text()}`);
    }

    const lines = (await response.text())
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);

    const files: FileListRow[] = [];
    for (const line of lines) {
        const parsed = JSON.parse(line) as { type: string; data?: FileListRow };
        if (parsed.type === 'file' && parsed.data) {
            files.push(parsed.data);
        }
    }

    return files;
}

export async function waitForCondition(
    condition: () => Promise<boolean>,
    timeoutMs: number,
    intervalMs = 500,
    timeoutMessage = 'Condition timed out'
): Promise<void> {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
        if (await condition()) {
            return;
        }
        await sleep(intervalMs);
    }
    throw new Error(timeoutMessage);
}

export async function waitForRemoteFile(
    api: APIRequestContext,
    targetFileName: string,
    shouldExist: boolean,
    timeoutMs = 30_000
): Promise<void> {
    await waitForCondition(
        async () => {
            const response = await api.get('/api/v1/p2p/remote-files');
            if (!response.ok()) {
                return false;
            }
            const files = await response.json() as Array<{ file_path: string; display_file_path: string }>;
            const exists = files.some((f) => {
                const filePath = f.display_file_path || f.file_path || '';
                return filePath.includes(targetFileName);
            });
            return shouldExist ? exists : !exists;
        },
        timeoutMs,
        500,
        shouldExist
            ? `Timed out waiting for remote file "${targetFileName}" to appear`
            : `Timed out waiting for remote file "${targetFileName}" to disappear`
    );
}

export function dumpInstanceLogs(instance: FileFridgeInstance): string {
    return [
        `Base URL: ${instance.baseUrl}`,
        `Database: ${instance.databasePath}`,
        `Scheduler DB: ${instance.schedulerPath}`,
        '--- STDOUT ---',
        instance.stdout.join(''),
        '--- STDERR ---',
        instance.stderr.join(''),
    ].join('\n');
}

function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
