#!/usr/bin/env node
/**
 * 启动前自检：Node 版本、模型资产、ORT 运行时、桥与 seekdb 连通性。
 * 不修改任何东西，只报告。
 */
import { existsSync, statSync } from 'node:fs';
import { connect } from 'node:net';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const bridgeUrl = process.env.BRIDGE_URL ?? 'http://127.0.0.1:8787';
const seekdbPort = Number(process.env.SEEKDB_PORT ?? '2881');
let problems = 0;
const ok = (m) => console.log(`  OK    ${m}`);
const warn = (m) => console.log(`  WARN  ${m}`);
const bad = (m) => {
  problems++;
  console.log(`  FAIL  ${m}`);
};

const major = Number(process.versions.node.split('.')[0]);
if (major >= 20) ok(`Node ${process.versions.node}`);
else bad(`Node ${process.versions.node} 太旧，需要 >= 20`);

const modelDir = join(root, 'web/public/models/Xenova/bge-small-zh-v1.5');
for (const rel of ['config.json', 'tokenizer.json', 'onnx/model_uint8.onnx', 'onnx/model_fp16.onnx']) {
  if (existsSync(join(modelDir, rel))) ok(`模型资产 ${rel}`);
  else bad(`缺少 ${rel}（运行 npm run fetch-model）`);
}

for (const rel of ['ort-wasm-simd-threaded.jsep.mjs', 'ort-wasm-simd-threaded.jsep.wasm']) {
  const p = join(root, 'node_modules/@huggingface/transformers/dist', rel);
  if (existsSync(p)) ok(`ORT 运行时 ${rel}（${(statSync(p).size / 1024 / 1024).toFixed(1)} MiB）`);
  else bad(`缺少 ORT 运行时 ${rel}（重新 npm install）`);
}

const portOpen = (port) =>
  new Promise((resolveProbe) => {
    const socket = connect({ host: '127.0.0.1', port, timeout: 1200 });
    socket.once('connect', () => {
      socket.destroy();
      resolveProbe(true);
    });
    socket.once('timeout', () => {
      socket.destroy();
      resolveProbe(false);
    });
    socket.once('error', () => resolveProbe(false));
  });

if (await portOpen(seekdbPort)) ok(`seekdb 监听 127.0.0.1:${seekdbPort}`);
else warn(`seekdb 未监听 127.0.0.1:${seekdbPort}（先 ./scripts/start-seekdb.sh）`);

try {
  const res = await fetch(`${bridgeUrl}/api/health`, { signal: AbortSignal.timeout(2000) });
  const body = await res.json();
  if (res.ok && body.ok) {
    ok(`桥 ${bridgeUrl} 健康（seekdb ${body.databaseVersion ?? '?'}，${body.documents} 段）`);
  } else {
    bad(`桥返回 ${res.status}`);
  }
} catch {
  warn(`桥 ${bridgeUrl} 未启动（先 npm run bridge）`);
}

console.log(problems === 0 ? '\n自检通过；WARN 项需要对应进程起来。' : `\n自检有 ${problems} 项 FAIL。`);
process.exit(problems === 0 ? 0 : 1);
