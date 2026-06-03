import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';

import { seedAuthToken } from './helpers';
import {
    bootstrapUserAndToken,
    createAuthedApi,
    createStorageAndPath,
    dumpInstanceLogs,
    listFiles,
    randomId,
    startFileFridgeInstance,
    stopFileFridgeInstance,
    triggerScan,
    waitForCondition,
    waitForRemoteFile,
} from './p2p-fixture';

test.describe.serial('P2P sharing v2 end-to-end (two instances)', () => {
    test('shares a scanned file over PSK network and removes it when unshared', async ({ browser }, testInfo) => {
        test.skip(
            process.env.PLAYWRIGHT_P2P_E2E !== '1',
            'Set PLAYWRIGHT_P2P_E2E=1 to run the two-instance P2P E2E flow.'
        );

        const instanceA = await startFileFridgeInstance('a');
        const instanceB = await startFileFridgeInstance('b');

        try {
            const aAuth = await bootstrapUserAndToken(instanceA.baseUrl);
            const bAuth = await bootstrapUserAndToken(instanceB.baseUrl);

            const apiA = await createAuthedApi(instanceA.baseUrl, aAuth.token);
            const apiB = await createAuthedApi(instanceB.baseUrl, bAuth.token);

            try {
                const psk = randomId('ff-p2p-psk');
                const fileName = `${randomId('shared-file')}.txt`;
                const filePath = path.join(instanceA.hotDir, fileName);

                const pathSetup = await createStorageAndPath(apiA, {
                    storageName: randomId('cold-location'),
                    coldPath: instanceA.coldDir,
                    pathName: randomId('hot-path'),
                    hotPath: instanceA.hotDir,
                });

                fs.writeFileSync(filePath, `p2p e2e payload ${randomId('content')}\n`, 'utf-8');

                await triggerScan(apiA, pathSetup.pathId);
                await waitForCondition(
                    async () => {
                        const files = await listFiles(
                            apiA,
                            `search=${encodeURIComponent(fileName)}&page_size=100`
                        );
                        return files.some((f) => !f.is_remote && (f.display_file_path || f.file_path).includes(fileName));
                    },
                    30_000,
                    500,
                    `Timed out waiting for local file ${fileName} to be indexed`
                );

                const networkPayloadA = {
                    network_name: 'File Fridge P2P',
                    listen_host: '127.0.0.1',
                    listen_port: instanceA.port,
                    enabled: true,
                    psk,
                };
                const networkPayloadB = {
                    network_name: 'File Fridge P2P',
                    listen_host: '127.0.0.1',
                    listen_port: instanceB.port,
                    enabled: true,
                    psk,
                };

                const netAResp = await apiA.post('/api/v1/p2p/network', { data: networkPayloadA });
                expect(netAResp.ok(), await netAResp.text()).toBeTruthy();
                const netBResp = await apiB.post('/api/v1/p2p/network', { data: networkPayloadB });
                expect(netBResp.ok(), await netBResp.text()).toBeTruthy();

                const joinResp = await apiB.post('/api/v1/p2p/peers/join', {
                    data: {
                        host: '127.0.0.1',
                        port: instanceA.port,
                        psk,
                        peer_name: 'Instance A',
                    },
                });
                expect(joinResp.ok(), await joinResp.text()).toBeTruthy();

                const syncResp = await apiB.post('/api/v1/p2p/sync');
                expect(syncResp.ok(), await syncResp.text()).toBeTruthy();

                await waitForRemoteFile(apiB, fileName, true, 30_000);

                const peersResp = await apiB.get('/api/v1/p2p/peers');
                expect(peersResp.ok(), await peersResp.text()).toBeTruthy();
                const peers = await peersResp.json() as Array<{ id: number; host: string; port: number }>;
                const peer = peers.find((p) => p.host === '127.0.0.1' && p.port === instanceA.port);
                expect(peer).toBeTruthy();
                const remotePeerId = peer!.id;

                const pageB = await browser.newPage();
                await seedAuthToken(pageB, bAuth.token);

                await pageB.goto(`${instanceB.baseUrl}/p2p`);
                await expect(pageB.locator('#p2p-peers-list')).toContainText(`127.0.0.1:${instanceA.port}`);

                await pageB.goto(`${instanceB.baseUrl}/files`);
                await pageB.fill('#search_input', fileName);
                await pageB.press('#search_input', 'Enter');
                await expect(pageB.getByText(fileName).first()).toBeVisible();

                await pageB.selectOption('#storage_filter', `peer:${remotePeerId}`);
                await expect(pageB).toHaveURL(new RegExp(`remote_peer_id=${remotePeerId}`));
                await expect(pageB.getByText(fileName).first()).toBeVisible();

                const localFiles = await listFiles(apiA, `search=${encodeURIComponent(fileName)}&page_size=100`);
                const local = localFiles.find((f) => !f.is_remote && (f.display_file_path || f.file_path).includes(fileName));
                expect(local).toBeTruthy();
                const inventoryId = Number(local!.id);
                expect(Number.isInteger(inventoryId)).toBeTruthy();

                const unshareResp = await apiA.post(`/api/v1/files/${inventoryId}/share`, {
                    data: { is_shareable: false },
                });
                expect(unshareResp.ok(), await unshareResp.text()).toBeTruthy();

                const syncResp2 = await apiB.post('/api/v1/p2p/sync');
                expect(syncResp2.ok(), await syncResp2.text()).toBeTruthy();
                await waitForRemoteFile(apiB, fileName, false, 30_000);

                await pageB.reload();
                await pageB.selectOption('#storage_filter', `peer:${remotePeerId}`);
                await pageB.fill('#search_input', fileName);
                await pageB.press('#search_input', 'Enter');
                await expect(pageB.getByText(fileName)).toHaveCount(0);

                await pageB.close();
            } finally {
                await apiA.dispose();
                await apiB.dispose();
            }
        } catch (error) {
            await testInfo.attach('instance-a-logs.txt', {
                body: dumpInstanceLogs(instanceA),
                contentType: 'text/plain',
            });
            await testInfo.attach('instance-b-logs.txt', {
                body: dumpInstanceLogs(instanceB),
                contentType: 'text/plain',
            });
            throw error;
        } finally {
            await stopFileFridgeInstance(instanceA);
            await stopFileFridgeInstance(instanceB);
        }
    });
});

