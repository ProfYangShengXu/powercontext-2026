/**
 * 集成测试（需要先启动 seekdb + 桥）。
 *
 * 它模拟浏览器的行为：本地算出向量，再只把 {id,text,vector,meta} 发给桥。
 * 这里用 Node 版的 transformers 只为“替浏览器生成请求向量”，
 * 应用运行时的推理仍然只在 web Worker 里。测试本身不进入应用。
 *
 * 运行：node tests/integration.mjs   （或 npm run test:integration）
 */
import { pipeline, env } from '@huggingface/transformers';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const BRIDGE = process.env.BRIDGE_URL ?? 'http://127.0.0.1:8787';
const stamp = Date.now().toString(36);
const KB1 = `itest-${stamp}`;
const KB2 = `itest2-${stamp}`;

env.allowRemoteModels = false;
env.localModelPath = join(root, '.models') + '/';
env.allowLocalModels = true;

let passed = 0;
let failed = 0;
function check(name, condition, detail = '') {
  if (condition) {
    passed++;
    console.log(`  ✓ ${name}`);
  } else {
    failed++;
    console.log(`  ✗ ${name} ${detail}`);
  }
}

async function post(path, body) {
  const res = await fetch(`${BRIDGE}${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  const json = await res.json().catch(() => null);
  return { status: res.status, json };
}

async function get(path) {
  const res = await fetch(`${BRIDGE}${path}`);
  return { status: res.status, json: await res.json().catch(() => null) };
}

const DOCS = [
  { id: 'd1', source: 'seekdb.md', text: 'seekdb 的 HYBRID_SEARCH 表函数用一条 SQL 同时跑全文臂与向量臂，融合节点用 RRF 把两路排名合成。' },
  { id: 'd2', source: 'seekdb.md', text: '全文索引使用 ngram 分词器，中文按字片段匹配；向量索引使用 HNSW，表必须是 heap 表。' },
  { id: 'd3', source: 'webgpu.md', text: 'WebGPU 后端使用 fp16 权重；设备不支持 shader-f16 时自动降级到 WASM 的 uint8 量化模型。' },
  { id: 'd4', source: 'bridge.md', text: 'Node 薄桥只做参数绑定与协议转换，禁止排序、融合、过滤和 top-k 截断。' },
  { id: 'd5', source: 'quant.md', text: '量化压缩比是量化前体积除以量化后体积；fp16 约两倍，uint8 约四倍。' },
  { id: 'd6', source: 'misc.md', text: '产品型号 OB-4.6.0 的发布说明只出现在这一篇文档里，适合做关键词命中回归。' },
];

const parser = await pipeline('feature-extraction', 'bge-small-zh-v1.5', { dtype: 'uint8', device: 'cpu' });
async function embed(texts) {
  const out = await parser(texts, { pooling: 'cls', normalize: true });
  const dim = out.dims[out.dims.length - 1];
  const vectors = [];
  for (let i = 0; i < texts.length; i++) vectors.push(Array.from(out.data.slice(i * dim, (i + 1) * dim)));
  return { vectors, dim };
}

async function ingest(kb, docs) {
  const { vectors } = await embed(docs.map((d) => d.text));
  const chunks = docs.map((d, i) => ({
    id: `c${i}-${d.id}-${kb}`,
    collection: kb,
    text: d.text,
    vector: vectors[i].map((x) => Number(x.toFixed(6))),
    metadata: { source: d.source, index: i },
  }));
  const r = await post('/api/ingest', { chunks });
  return { ...r, chunks };
}

async function main() {
  console.log(`桥：${BRIDGE}`);
  const health = await get('/api/health');
  check('健康检查可达', health.status === 200 && health.json?.ok === true, JSON.stringify(health.json));
  check('数据库是 seekdb', String(health.json?.databaseVersion ?? '').includes('seekdb'));

  console.log('\n[1] 写入 + 发布 + 检索');
  const a = await ingest(KB1, DOCS);
  check('ingest 返回写入条数', a.status === 200 && a.json?.written === DOCS.length, JSON.stringify(a.json));
  const pub = await post('/api/publish', {});
  check('publish 成功', pub.status === 200 && typeof pub.json?.publishedAt === 'string', JSON.stringify(pub.json));
  check('publish 做了可见性探测', typeof pub.json?.readyAfterMs === 'number');

  const q1 = await embed(['seekdb 怎么融合全文和向量']);
  const s1 = await post('/api/search', {
    text: 'seekdb 怎么融合全文和向量',
    vector: q1.vectors[0],
    topK: 5,
    collection: KB1,
    debug: true,
  });
  check('search 返回 200', s1.status === 200, JSON.stringify(s1.json));
  const hits = s1.json?.hits ?? [];
  check('search 有结果', hits.length > 0);
  check('命中包含原文', hits.every((h) => typeof h.text === 'string' && h.text.length > 0));
  check('返回顺序是分数降序（桥没有重排）', hits.every((h, i) => i === 0 || hits[i - 1].score >= h.score));
  check('单条 SQL 是 HYBRID_SEARCH', String(s1.json?.debug?.sql ?? '').includes('HYBRID_SEARCH'));
  check('SQL 里用了 RRF', String(s1.json?.debug?.sql ?? '').includes('rrf'));
  check('SQL 里带 kb 过滤', String(s1.json?.debug?.sql ?? '').includes('kb_id'));
  check('结果都来自当前知识库', hits.every((h) => h.collection === KB1));

  console.log('\n[2] 关键词回归：纯向量查不到、混合应命中');
  const q2 = await embed(['OB-4.6.0']);
  const s2 = await post('/api/search', {
    text: 'OB-4.6.0',
    vector: q2.vectors[0],
    topK: 5,
    collection: KB1,
    debug: true,
  });
  const ids2 = (s2.json?.hits ?? []).map((h) => h.text);
  check('关键词命中了型号段落', ids2.some((t) => t.includes('OB-4.6.0')), JSON.stringify(ids2));

  console.log('\n[3] 幂等：同一批再导一次');
  const before = await get('/api/overview');
  await ingest(KB1, DOCS);
  await post('/api/publish', {});
  const after = await get('/api/overview');
  const count1 = before.json.collections.find((c) => c.id === KB1)?.documents ?? 0;
  const count2 = after.json.collections.find((c) => c.id === KB1)?.documents ?? 0;
  check(`重复导入不增行（${count1} → ${count2}）`, count1 === count2 && count1 === DOCS.length);

  console.log('\n[4] 知识库隔离');
  await ingest(KB2, [
    { id: 'x1', source: 'other.md', text: '这是一篇与检索无关的财务说明，讲预算和报销流程。' },
    { id: 'x2', source: 'other.md', text: 'seekdb 混合检索在另一个知识库里不应该被串出来。' },
  ]);
  await post('/api/publish', {});
  const s3 = await post('/api/search', {
    text: 'seekdb 混合检索',
    vector: q1.vectors[0],
    topK: 10,
    collection: KB2,
    debug: true,
  });
  check('KB2 的查询只返回 KB2 的行', (s3.json?.hits ?? []).every((h) => h.collection === KB2));

  console.log('\n[5] 输入边界');
  const badDim = await post('/api/ingest', {
    chunks: [{ id: 'bad1', collection: KB1, text: 'x', vector: [1, 2, 3], metadata: {} }],
  });
  check('维度不符被拒（400）', badDim.status === 400, JSON.stringify(badDim.json));
  const noKb = await post('/api/search', {
    text: 'x',
    vector: q1.vectors[0],
    topK: 5,
  });
  check('缺 collection 被拒（400）', noKb.status === 400);
  const emptyQ = await post('/api/search', {
    text: '   ',
    vector: q1.vectors[0],
    topK: 5,
    collection: KB1,
  });
  check('空查询被拒（400）', emptyQ.status === 400);

  console.log('\n[6] 含引号/反斜杠的查询');
  const nasty = `what's "vector" \\ 测试`;
  const qn = await embed([nasty]);
  const sn = await post('/api/search', { text: nasty, vector: qn.vectors[0], topK: 3, collection: KB1, debug: true });
  check('引号查询不报错', sn.status === 200, JSON.stringify(sn.json));

  console.log(`\n结果：${passed} passed, ${failed} failed`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((err) => {
  console.error('\n集成测试异常：', err);
  process.exit(1);
});
