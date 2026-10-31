/**
 * 量化质量评测（离线工具，不属于应用运行路径）。
 *
 * 目的：在完全相同的语料 / 查询 / qrels 上，比较 fp32 / fp16 / int8 / uint8
 * 四套权重的纯向量召回质量，用来回答题面「检索质量变化」。
 *
 * 说明：
 *   - 这里只测「嵌入质量」，所以只看向量排序，不叠加全文臂；
 *     应用里的混合检索是 seekdb 内固定的 RRF，不会改变量化的相对差异。
 *   - 端侧吞吐/延迟不在这里测：那是浏览器 + 设备相关的数字，页面会单独计时。
 *   - 运行：npm run eval（需要先 npm run fetch-model --all）
 */
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { pipeline, env } from '@huggingface/transformers';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const data = JSON.parse(readFileSync(join(here, 'data.json'), 'utf8'));
const DTYPES = ['fp32', 'fp16', 'int8', 'uint8'];
const K_VALUES = [5, 10];
const BATCH = 16;

env.allowRemoteModels = false;
env.localModelPath = join(root, '.models') + '/';
env.allowLocalModels = true;

if (!existsSync(join(root, '.models', 'bge-small-zh-v1.5', 'onnx', 'model.onnx'))) {
  console.error('缺少本地模型：先运行 npm run fetch-model --all');
  process.exit(1);
}

function normalize(v) {
  let n = 0;
  for (const x of v) n += x * x;
  const inv = 1 / Math.sqrt(n || 1);
  return v.map((x) => x * inv);
}

function dot(a, b) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * b[i];
  return s;
}

function dcg(rels) {
  return rels.reduce((s, r, i) => s + r / Math.log2(i + 2), 0);
}

function metrics(rankedIds, relevant) {
  const relSet = new Set(relevant);
  const relFlags = rankedIds.map((id) => (relSet.has(id) ? 1 : 0));
  const out = {};
  for (const k of K_VALUES) {
    const hit = relFlags.slice(0, k).reduce((s, x) => s + x, 0);
    out[`recall@${k}`] = hit / Math.max(1, relevant.length);
  }
  const ideal = relFlags
    .slice()
    .sort((a, b) => b - a)
    .slice(0, 10);
  const idealDcg = dcg(ideal);
  out['ndcg@10'] = idealDcg ? dcg(relFlags.slice(0, 10)) / idealDcg : 0;
  const first = relFlags.findIndex((x) => x === 1);
  out['mrr@10'] = first >= 0 ? 1 / (first + 1) : 0;
  return out;
}

async function embedAll(pipe, texts) {
  const vectors = [];
  const started = performance.now();
  for (let i = 0; i < texts.length; i += BATCH) {
    const batch = texts.slice(i, i + BATCH);
    const out = await pipe(batch, { pooling: 'cls', normalize: true });
    const dim = out.dims[out.dims.length - 1];
    for (let j = 0; j < batch.length; j++) {
      vectors.push(normalize(Array.from(out.data.slice(j * dim, (j + 1) * dim))));
    }
  }
  return { vectors, ms: performance.now() - started };
}

const corpus = data.corpus;
const queries = data.queries;
const results = {};

for (const dtype of DTYPES) {
  let pipe;
  try {
    pipe = await pipeline('feature-extraction', 'bge-small-zh-v1.5', { dtype, device: 'cpu' });
  } catch (err) {
    console.log(`[eval] ${dtype} 不可用：${err.message}`);
    results[dtype] = { error: err.message };
    continue;
  }
  const loadStart = performance.now();
  const corpusEmb = await embedAll(pipe, corpus.map((d) => d.text));
  const queryEmb = await embedAll(pipe, queries.map((q) => q.text));
  const loadMs = performance.now() - loadStart;

  const perQuery = [];
  for (let qi = 0; qi < queries.length; qi++) {
    const q = queries[qi];
    const qv = queryEmb.vectors[qi];
    const ranked = corpus
      .map((d, di) => ({ id: d.id, score: dot(qv, corpusEmb.vectors[di]) }))
      .sort((a, b) => b.score - a.score || a.id.localeCompare(b.id))
      .map((x) => x.id);
    perQuery.push({ id: q.id, ...metrics(ranked, q.relevant), top5: ranked.slice(0, 5) });
  }

  const avg = (key) => perQuery.reduce((s, r) => s + r[key], 0) / perQuery.length;
  const summary = {
    'recall@5': +avg('recall@5').toFixed(4),
    'recall@10': +avg('recall@10').toFixed(4),
    'ndcg@10': +avg('ndcg@10').toFixed(4),
    'mrr@10': +avg('mrr@10').toFixed(4),
    embedMsPerDoc: +(corpusEmb.ms / corpus.length).toFixed(1),
    totalMs: Math.round(loadMs),
    queries: perQuery,
  };
  results[dtype] = summary;
  console.log(
    `[eval] ${dtype.padEnd(5)} R@5=${summary['recall@5']} R@10=${summary['recall@10']} ` +
      `nDCG@10=${summary['ndcg@10']} MRR=${summary['mrr@10']} (${summary.embedMsPerDoc}ms/段)`,
  );
}

const baseline = results.fp32;
if (baseline && !baseline.error) {
  for (const dtype of DTYPES) {
    const r = results[dtype];
    if (!r || r.error) continue;
    r.deltaVsFp32 = {
      'recall@5': +(r['recall@5'] - baseline['recall@5']).toFixed(4),
      'recall@10': +(r['recall@10'] - baseline['recall@10']).toFixed(4),
      'ndcg@10': +(r['ndcg@10'] - baseline['ndcg@10']).toFixed(4),
    };
  }
}

const report = {
  generatedAt: new Date().toISOString(),
  dataset: { corpus: corpus.length, queries: queries.length, model: 'Xenova/bge-small-zh-v1.5' },
  method: '纯向量排序（cosine），top-10；qrels 为人工标注；指标取 20 条查询的算术平均',
  results,
};
writeFileSync(join(here, 'results.json'), JSON.stringify(report, null, 2));

const rows = DTYPES.filter((d) => results[d] && !results[d].error).map((d) => {
  const r = results[d];
  return `| ${d} | ${r['recall@5']} | ${r['recall@10']} | ${r['ndcg@10']} | ${r['mrr@10']} | ${r.embedMsPerDoc} |`;
});
const md = [
  `# 量化质量评测（${report.generatedAt}）`,
  '',
  `数据集：${corpus.length} 段语料 / ${queries.length} 条查询，模型 ${report.dataset.model}。`,
  '',
  '| dtype | Recall@5 | Recall@10 | nDCG@10 | MRR@10 | 向量化 ms/段（Node CPU） |',
  '|---|---|---|---|---|---|',
  ...rows,
  '',
  '> Node CPU 上的毫秒数只用于横向比较，不代表浏览器端性能。',
].join('\n');
writeFileSync(join(here, 'results.md'), md + '\n');
console.log('\n' + md);
