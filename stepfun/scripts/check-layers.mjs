#!/usr/bin/env node
/**
 * 分层体检（机器可检查）。
 *
 * 判据对应架构文档 §2.5⑤：
 *   A  core/ + ports/ 的 import 行只允许相对路径（零外部依赖）
 *   B  core/ + ports/ 不出现实现技术词
 *   C  只有 bootstrap/index.ts new 具体实现
 *   D  Node 侧（bridge）的 import 图里没有 transformers / onnxruntime
 *   E  SQL 只出现在 store-seekdb 适配器；bridge 没有排序/截断/融合
 *
 * 退出码：0 全过；1 有失败（CI 可直接用）。
 */
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, statSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
let failed = 0;

function pass(name, detail = '') {
  console.log(`PASS  ${name}${detail ? ` — ${detail}` : ''}`);
}
function fail(name, detail) {
  failed++;
  console.log(`FAIL  ${name} — ${detail}`);
}

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry.startsWith('.')) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (/\.(ts|mts|js|mjs)$/.test(entry)) out.push(full);
  }
  return out;
}
const read = (f) => readFileSync(f, 'utf8');
const stripComments = (code) =>
  code.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');

const coreFiles = walk(join(root, 'core'));
const portFiles = walk(join(root, 'ports'));
const stableFiles = [...coreFiles, ...portFiles];

// A + 记录 import 行哈希
const importLines = [];
const external = [];
for (const file of stableFiles) {
  for (const line of read(file).split('\n')) {
    if (!/^\s*(import|export)\s/.test(line)) continue;
    importLines.push(line.trim());
    const specs = [...line.matchAll(/from\s+['"]([^'"]+)['"]/g)].map((m) => m[1]);
    for (const spec of specs) {
      if (!spec.startsWith('.')) external.push(`${relative(root, file)}: ${spec}`);
    }
  }
}
if (external.length) fail('core/ports 零外部 import', external.join('; '));
else pass('core/ports 零外部 import', `${stableFiles.length} 个文件`);

const importHash = createHash('sha256').update(importLines.join('\n')).digest('hex');
console.log(`      core+ports import 行 sha256 = ${importHash}`);
console.log(`      （换模型/换后端/换库后重跑，应保持不变）`);

// B 技术词
const forbidden = /\b(webgpu|wasm|onnx|onnxruntime|transformers|seekdb|mysql|sql|dtype|hnsw|rrf|faiss)\b/i;
const leaks = [];
for (const file of stableFiles) {
  const code = stripComments(read(file));
  const m = code.match(forbidden);
  if (m) leaks.push(`${relative(root, file)}: ${m[0]}`);
}
if (leaks.length) fail('core/ports 不含实现技术词', leaks.join('; '));
else pass('core/ports 不含实现技术词');

// C 唯一装配点
const newPattern = /new\s+(KnowledgeBase|TransformersEmbedAdapter|HttpStoreAdapter|SeekdbStore)\b/;
const news = [];
for (const file of walk(root).filter((f) => !f.includes('/web-dist/'))) {
  if (newPattern.test(read(file))) news.push(relative(root, file));
}
if (news.length === 1 && news[0] === 'bootstrap/index.ts') {
  pass('唯一装配点', 'bootstrap/index.ts');
} else {
  fail('唯一装配点', `new 具体实现出现在：${news.join(', ') || '无'}`);
}

// D Node 侧 import 图
const TRANSITIVE_SKIP = new Set(['seekdb', 'mysql2']);
const seen = new Set();
const heavy = [];
function follow(file) {
  if (seen.has(file)) return;
  seen.add(file);
  const dir = dirname(file);
  for (const m of read(file).matchAll(/(?:from\s*|import\s*\(\s*)['"]([^'"]+)['"]/g)) {
    const spec = m[1];
    if (spec.startsWith('.')) {
      let next = resolve(dir, spec);
      if (!next.endsWith('.ts')) next = next.replace(/\.js$/, '.ts');
      try {
        if (statSync(next).isFile()) follow(next);
      } catch {
        /* 动态变量路径，跳过 */
      }
    } else if (!TRANSITIVE_SKIP.has(spec)) {
      heavy.push(`${relative(root, file)} -> ${spec}`);
    }
  }
}
follow(join(root, 'bridge/main.ts'));
const banned = heavy.filter((s) => /transformers|onnxruntime|onnx/i.test(s));
if (banned.length) fail('Node 桥 import 图不含推理依赖', banned.join('; '));
else pass('Node 桥 import 图不含推理依赖', `${seen.size} 个模块`);

// E SQL 只在适配器 + 桥不排序
const storeFile = join(root, 'adapters/store-seekdb/index.ts');
if (/HYBRID_SEARCH/.test(read(storeFile)) && /rrf/.test(read(storeFile))) {
  pass('SQL/融合只出现在 store-seekdb 适配器');
} else {
  fail('SQL/融合只出现在 store-seekdb 适配器', '未找到 HYBRID_SEARCH/rrf');
}
const bridgeFiles = walk(join(root, 'bridge'));
const impure = [];
for (const file of bridgeFiles) {
  const code = stripComments(read(file));
  if (/\.sort\(/.test(code)) impure.push(`${relative(root, file)}: .sort(`);
  if (/\.slice\(\s*0\s*,/.test(code)) impure.push(`${relative(root, file)}: .slice(0,`);
  if (/HYBRID_SEARCH/.test(code)) impure.push(`${relative(root, file)}: SQL 出现在桥里`);
  if (/\brrf\b/i.test(code)) impure.push(`${relative(root, file)}: rrf 出现在桥里`);
}
if (impure.length) fail('桥不排序/不融合/不截断', impure.join('; '));
else pass('桥不排序/不融合/不截断');

// 端口实现检查
const embedImpl = read(join(root, 'adapters/embed-transformers/adapter.ts'));
const storeHttp = read(join(root, 'adapters/store-http/index.ts'));
const storeSeekdb = read(storeFile);
if (/implements\s+EmbedPort/.test(embedImpl)) pass('EmbedPort 有浏览器实现');
else fail('EmbedPort 有浏览器实现', 'adapter.ts 未 implements EmbedPort');
if (/implements\s+StorePort/.test(storeHttp) && /implements\s+StorePort/.test(storeSeekdb)) {
  pass('StorePort 有两个实现', 'browser/http + node/seekdb');
} else {
  fail('StorePort 有两个实现', '缺少 implements StorePort');
}

// 文档/配置里不出现本地绝对路径或凭据
const scanFiles = walk(join(root, 'web')).concat(
  walk(join(root, 'bridge')),
  walk(join(root, 'core')),
  walk(join(root, 'ports')),
  walk(join(root, 'adapters')),
  walk(join(root, 'bootstrap')),
);
const abs = scanFiles.filter((f) => /\/mnt\/|C:\\\\Users|D:\\\\pc-work/.test(read(f)));
if (abs.length) fail('源码不含本机绝对路径', abs.map((f) => relative(root, f)).join(', '));
else pass('源码不含本机绝对路径');

mkdirSync(join(root, 'docs/evidence'), { recursive: true });
writeFileSync(
  join(root, 'docs/evidence/layer-check.json'),
  JSON.stringify(
    {
      generatedAt: new Date().toISOString(),
      importLineHash: importHash,
      stableFiles: stableFiles.map((f) => relative(root, f)),
      checks: { externalImports: external.length, techWordLeaks: leaks.length, newSites: news },
    },
    null,
    2,
  ),
);
console.log(`\n${failed === 0 ? '分层体检：全部通过' : `分层体检：${failed} 项失败`}`);
process.exit(failed === 0 ? 0 : 1);
