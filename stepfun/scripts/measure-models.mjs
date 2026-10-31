#!/usr/bin/env node
/**
 * 实测模型文件体积：直接 stat 文件字节数，不抄任何文档里的数字。
 * 口径：同一模型目录下「权重 + config + tokenizer + 词表」的总字节数。
 */
import { existsSync, mkdirSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const modelDir = join(root, '.models', 'bge-small-zh-v1.5');
const evidenceDir = join(root, 'docs', 'evidence');

if (!existsSync(modelDir)) {
  console.error('缺少 .models/bge-small-zh-v1.5；先运行 npm run fetch-model --all');
  process.exit(1);
}

const commonFiles = ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'vocab.txt'];
const commonBytes = commonFiles.reduce((n, f) => {
  const p = join(modelDir, f);
  return n + (existsSync(p) ? statSync(p).size : 0);
}, 0);

const variants = [
  { dtype: 'fp32', file: 'model.onnx', label: '基线（未量化）' },
  { dtype: 'fp16', file: 'model_fp16.onnx', label: 'FP16（WebGPU）' },
  { dtype: 'int8', file: 'model_int8.onnx', label: 'INT8' },
  { dtype: 'uint8', file: 'model_uint8.onnx', label: 'UINT8（WASM）' },
  { dtype: 'q8', file: 'model_quantized.onnx', label: 'Q8（transformers.js 默认 wasm）' },
];

const rows = [];
for (const v of variants) {
  const p = join(modelDir, 'onnx', v.file);
  if (!existsSync(p)) {
    rows.push({ ...v, missing: true });
    continue;
  }
  const onnxBytes = statSync(p).size;
  rows.push({
    ...v,
    onnxBytes,
    totalBytes: onnxBytes + commonBytes,
    onnxMiB: +(onnxBytes / 1024 / 1024).toFixed(2),
    totalMiB: +((onnxBytes + commonBytes) / 1024 / 1024).toFixed(2),
  });
}
const baseline = rows.find((r) => r.dtype === 'fp32' && !r.missing);
for (const r of rows) {
  if (r.missing || !baseline) continue;
  r.ratioVsFp32 = +(baseline.totalBytes / r.totalBytes).toFixed(2);
}

const report = {
  measuredAt: new Date().toISOString(),
  modelDir: '.models/bge-small-zh-v1.5',
  modelId: 'Xenova/bge-small-zh-v1.5',
  accounting: '权重 ONNX + config.json + tokenizer.json + tokenizer_config.json + vocab.txt',
  commonBytes,
  rows,
  files: readdirSync(join(modelDir, 'onnx')).map((f) => ({
    file: `onnx/${f}`,
    bytes: statSync(join(modelDir, 'onnx', f)).size,
  })),
};
mkdirSync(evidenceDir, { recursive: true });
writeFileSync(join(evidenceDir, 'model-sizes.json'), JSON.stringify(report, null, 2));

const header =
  '| dtype | 权重文件 | ONNX MiB | 含 tokenizer 总 MiB | 压缩比（fp32/本行） |\n|---|---|---|---|---|';
const body = rows
  .filter((r) => !r.missing)
  .map((r) => `| ${r.dtype} | ${r.file} | ${r.onnxMiB} | ${r.totalMiB} | ${r.ratioVsFp32 ?? '-'} |`)
  .join('\n');
const missing = rows.filter((r) => r.missing).map((r) => r.file);
const md = [
  '# 模型体积实测（' + report.measuredAt + '）',
  '',
  `模型：${report.modelId}；口径：${report.accounting}。`,
  '',
  header,
  body,
  '',
  missing.length ? `缺失变体（未下载）：${missing.join(', ')}` : '',
  '> 数字来自 `fs.statSync().size`，不是文档抄录；可用 `npm run measure-models` 复测。',
].join('\n');
writeFileSync(join(evidenceDir, 'model-sizes.md'), md + '\n');
console.log(md);
