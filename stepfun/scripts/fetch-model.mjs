#!/usr/bin/env node
/**
 * 一次准备好「同源资产」：
 *   ORT 运行时由 vite 插件从 node_modules 直出（见 vite.config.ts），不需要拷贝。
 *   把 ONNX 模型放到 web/public/models/<model-id>/（默认只取 fp16 + uint8，两套合计约 69MB）
 *
 * 用法：
 *   npm run fetch-model            # fp16 + uint8（应用运行所需）
 *   npm run fetch-model -- --all   # 再加 fp32 / int8 / q8（离线评测用）
 */
import { copyFileSync, existsSync, mkdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const modelId = 'Xenova/bge-small-zh-v1.5';
const repoDir = 'bge-small-zh-v1.5';
const localSrc = join(root, '.models', repoDir);
const outDir = join(root, 'web', 'public', 'models', ...modelId.split('/'));
const all = process.argv.includes('--all');

const baseFiles = ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'vocab.txt'];
const onnxFiles = all
  ? ['model.onnx', 'model_fp16.onnx', 'model_int8.onnx', 'model_uint8.onnx', 'model_quantized.onnx']
  : ['model_fp16.onnx', 'model_uint8.onnx'];

function ensureDir(p) {
  mkdirSync(p, { recursive: true });
}

function copyOrDownload(relPath, destPath) {
  const cached = join(localSrc, relPath);
  ensureDir(dirname(destPath));
  if (existsSync(cached)) {
    copyFileSync(cached, destPath);
    return 'cache';
  }
  if (existsSync(destPath)) return 'exists';
  const url = `https://huggingface.co/${modelId}/resolve/main/${relPath}`;
  console.log(`  下载 ${relPath} …`);
  execFileSync('curl', ['-sL', '--retry', '3', '-o', destPath, url], { stdio: 'inherit' });
  return 'download';
}

function sizeOf(p) {
  return statSync(p).size;
}

ensureDir(outDir);

console.log('模型 →', outDir);
let total = 0;
for (const rel of [...baseFiles, ...onnxFiles.map((f) => `onnx/${f}`)]) {
  const dest = join(outDir, rel);
  const how = copyOrDownload(rel, dest);
  const bytes = sizeOf(dest);
  total += bytes;
  console.log(`  ${how.padEnd(8)} ${rel.padEnd(26)} ${(bytes / 1024 / 1024).toFixed(2)} MiB`);
}

const manifest = {
  generatedAt: new Date().toISOString(),
  modelId,
  files: [...baseFiles, ...onnxFiles.map((f) => `onnx/${f}`)],
  totalBytes: total,
};
writeFileSync(join(outDir, 'ASSET-MANIFEST.json'), JSON.stringify(manifest, null, 2));
console.log(`\n完成：${(total / 1024 / 1024).toFixed(1)} MiB。启动 npm run bridge 与 npm run dev。`);
