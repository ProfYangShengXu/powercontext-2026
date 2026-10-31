/** Node 薄桥入口。装配全部交给 bootstrap/index.ts。 */
import { BRIDGE_SPEC, createBridgeRuntime } from '../bootstrap/index.js';
import { startBridge } from './server.js';

async function main(): Promise<void> {
  const { store } = await createBridgeRuntime();
  const server = await startBridge(store, {
    host: BRIDGE_SPEC.host,
    port: BRIDGE_SPEC.port,
    maxBodyBytes: BRIDGE_SPEC.maxBodyBytes,
  });
  const shutdown = async () => {
    console.log('\n[bridge] 正在关闭…');
    await server.close();
    await store.close();
    process.exit(0);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
  console.log(`[bridge] 健康检查：${server.url}/api/health`);
}

main().catch((err) => {
  console.error('[bridge] 启动失败：', err);
  process.exit(1);
});
