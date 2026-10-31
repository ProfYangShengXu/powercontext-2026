/**
 * UI（主线程）—— 只做展示与交互。
 *
 * 它不知道模型名、推理后端、SQL。所有实现选择都来自 bootstrap 的装配结果，
 * 所有检索规则都在 core。这里唯一“聪明”的地方是把 runtime 的诊断信息显示出来。
 */
import { createBrowserRuntime, MODEL_SPEC, type BrowserRuntime } from '../bootstrap/index.js';
import { stableHash } from '../core/chunker.js';
import type { PresentedHit } from '../core/presentation.js';

const $ = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`缺少 DOM 节点 #${id}`);
  return el as T;
};

const badge = $('badge');
const modelLabel = $('modelLabel');
const banner = $('banner');
const kbSelect = $<HTMLSelectElement>('kbSelect');
const newKb = $<HTMLInputElement>('newKb');
const addKbBtn = $<HTMLButtonElement>('addKbBtn');
const fileInput = $<HTMLInputElement>('fileInput');
const pasteText = $<HTMLTextAreaElement>('pasteText');
const pasteName = $<HTMLInputElement>('pasteName');
const importBtn = $<HTMLButtonElement>('importBtn');
const importStage = $('importStage');
const progressBar = $('progressBar');
const importStats = $('importStats');
const queryInput = $<HTMLInputElement>('queryInput');
const topK = $<HTMLSelectElement>('topK');
const searchBtn = $<HTMLButtonElement>('searchBtn');
const timing = $('timing');
const empty = $('empty');
const results = $<HTMLOListElement>('results');
const evidence = $('evidence');
const sqlText = $('sqlText');
const engineSqlText = $('engineSqlText');
const engineSqlBtn = $<HTMLButtonElement>('engineSqlBtn');
const diagPanel = $('diagPanel');
const diagText = $('diagText');
const exportDiag = $<HTMLButtonElement>('exportDiag');

const diagOn = new URLSearchParams(location.search).get('diag') === '1';
const fetchLog: Array<{ method: string; url: string; status: number; ms: number }> = [];

if (diagOn) {
  const original = window.fetch.bind(window);
  window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const started = performance.now();
    const res = await original(input, init);
    fetchLog.push({
      method: init?.method ?? 'GET',
      url: typeof input === 'string' ? input : input instanceof URL ? input.href : input.url,
      status: res.status,
      ms: Math.round(performance.now() - started),
    });
    return res;
  }) as typeof window.fetch;
}

let runtime: BrowserRuntime | null = null;
let kbId = 'default';
let busy = false;

function showBanner(text: string): void {
  banner.hidden = false;
  banner.textContent = text;
}

function clearBanner(): void {
  banner.hidden = true;
  banner.textContent = '';
}

function setBadge(kind: 'webgpu' | 'wasm' | 'bad', text: string): void {
  badge.className = `badge ${kind === 'bad' ? 'badge-bad' : kind === 'webgpu' ? 'badge-webgpu' : 'badge-wasm'}`;
  badge.textContent = text;
}

function formatScore(n: number): string {
  if (!Number.isFinite(n)) return '—';
  if (n === 0) return '0';
  return n >= 1 ? n.toFixed(4) : n.toPrecision(4);
}

function renderHits(hits: readonly PresentedHit[]): void {
  results.replaceChildren();
  for (const hit of hits) {
    const li = document.createElement('li');
    li.className = 'result';

    const head = document.createElement('div');
    head.className = 'result-head';

    const rank = document.createElement('span');
    rank.className = 'rank';
    rank.textContent = `#${hit.rank}`;

    const score = document.createElement('span');
    score.className = 'score';
    score.textContent = `score ${formatScore(hit.score)}`;

    const meta = document.createElement('span');
    meta.className = 'meta';
    const source = typeof hit.metadata.source === 'string' ? hit.metadata.source : '';
    const index = typeof hit.metadata.index === 'number' ? `#${hit.metadata.index}` : '';
    meta.textContent = `${hit.collection} · ${source} ${index}`.trim();

    const id = document.createElement('span');
    id.className = 'meta';
    id.textContent = hit.id;

    head.append(rank, score, meta, id);

    const body = document.createElement('div');
    body.className = 'result-text';
    body.textContent = hit.text;

    li.append(head, body);
    results.append(li);
  }
}

async function refreshCollections(): Promise<void> {
  if (!runtime) return;
  const overview = await runtime.store.overview();
  const ids = overview.collections.map((c) => c.id);
  if (!ids.includes(kbId)) {
    // 空库时保留当前输入值，方便先起名再导入。
    if (ids.length > 0) kbId = ids[0] as string;
  }
  const options = ids.length ? ids : [kbId];
  kbSelect.replaceChildren(
    ...options.map((id) => {
      const count = overview.collections.find((c) => c.id === id)?.documents ?? 0;
      const opt = document.createElement('option');
      opt.value = id;
      opt.textContent = `${id}（${count} 段）`;
      return opt;
    }),
  );
  kbSelect.value = kbId;
  importStats.textContent = `库内共 ${overview.documents} 段${
    overview.lastPublishedAt ? ` · 最近发布 ${new Date(overview.lastPublishedAt).toLocaleTimeString()}` : ''
  }`;
}

function setBusy(next: boolean, why = ''): void {
  busy = next;
  importBtn.disabled = next || !runtime;
  searchBtn.disabled = next || !runtime;
  importStage.textContent = why;
}

async function boot(): Promise<void> {
  try {
    runtime = await createBrowserRuntime();
    runtime.embed.onProgress((file, progress) => {
      badge.className = 'badge badge-pending';
      badge.textContent = `加载 ${file} ${Math.round(progress)}%`;
    });
    const info = await runtime.embed.ready();
    setBadge(info.device, `${info.device === 'webgpu' ? 'WebGPU' : 'WASM'} · ${info.dtype}`);
    modelLabel.textContent = `${info.modelId} · ${info.dim} 维 · 模型加载 ${info.loadMs}ms`;
    if (info.fallbackReason) {
      showBanner(`WebGPU 初始化失败，已自动降级为 WASM · uint8。原因：${info.fallbackReason}`);
    }
    await refreshCollections();
    setBusy(false);
    queryInput.focus();
    if (diagOn) {
      diagPanel.hidden = false;
      renderDiag(info);
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    setBadge('bad', '初始化失败');
    showBanner(
      `模型或桥初始化失败：${message}\n` +
        `请确认：① 桥已启动（npm run bridge）；② 已运行 npm run fetch-model 把模型放到 web/public/models/；` +
        `③ 浏览器控制台没有 CSP 拦截。`,
    );
  }
}

function renderDiag(info: unknown): void {
  if (!runtime) return;
  const last = runtime.store.lastRequest;
  const payload = {
    userAgent: navigator.userAgent,
    runtime: info,
    probe: runtime.probe,
    requested: runtime.requested,
    modelSpec: MODEL_SPEC,
    lastQuery: last
      ? {
          text: last.query,
          topK: last.topK,
          collection: last.collection,
          vectorLength: last.vector.length,
          vectorHead: Array.from(last.vector.slice(0, 6)).map((n) => Number(n.toFixed(5))),
          vectorChecksum: stableHash(Array.from(last.vector).join(',')),
        }
      : null,
    lastSql: runtime.store.lastSearchDebug?.sql ?? null,
    fetchLog,
  };
  diagText.textContent = JSON.stringify(payload, null, 2);
}

async function doImport(): Promise<void> {
  if (!runtime || busy) return;
  clearBanner();
  const docs: Array<{ name: string; text: string }> = [];
  for (const file of Array.from(fileInput.files ?? [])) {
    docs.push({ name: file.name, text: await file.text() });
  }
  if (pasteText.value.trim()) {
    docs.push({ name: pasteName.value.trim() || 'paste.md', text: pasteText.value });
  }
  if (!docs.length) {
    showBanner('先选择文件或粘贴一段文本。');
    return;
  }
  setBusy(true, '端上向量化…');
  progressBar.style.width = '0%';
  importStats.textContent = '';
  try {
    const report = await runtime.kb.importDocuments(kbId, docs, {
      onStage: (stage) => {
        importStage.textContent =
          stage === 'embed' ? 'Worker 端上向量化…' : stage === 'write' ? '写入 seekdb…' : '刷新索引并等待可检索…';
      },
      onBatch: (done, total, ms) => {
        progressBar.style.width = `${Math.round((done / total) * 100)}%`;
        importStage.textContent = `向量化 ${done}/${total} 段 · 本批 ${ms}ms`;
      },
    });
    progressBar.style.width = '100%';
    const reportText =
      `写入 ${report.chunks} 段 / ${report.documents} 个来源 · ` +
      `publish ${report.readyAfterMs}ms · 发布于 ${new Date(report.publishedAt).toLocaleTimeString()}`;
    fileInput.value = '';
    pasteText.value = '';
    await refreshCollections();
    // refreshCollections 会写「库内共 N 段」；导入结果要留在同一行，别被覆盖。
    importStats.textContent = `${reportText} · 库内共 ${(await runtime.store.overview()).documents} 段`;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    showBanner(`导入失败：${message}`);
    importStats.textContent = '';
  } finally {
    setBusy(false);
    importStage.textContent = '';
  }
}

async function doSearch(): Promise<void> {
  if (!runtime || busy) return;
  const query = queryInput.value.trim();
  if (!query) {
    queryInput.focus();
    return;
  }
  clearBanner();
  setBusy(true, '');
  searchBtn.disabled = true;
  try {
    const report = await runtime.kb.ask(kbId, query, Number(topK.value));
    timing.innerHTML = '';
    const parts: Array<[string, string]> = [
      ['查询向量化', `${report.embedMs.toFixed(1)} ms`],
      ['库内检索', `${report.searchMs.toFixed(1)} ms`],
      ['端到端', `${report.totalMs.toFixed(1)} ms`],
    ];
    for (const [label, value] of parts) {
      const item = document.createElement('span');
      item.innerHTML = `${label} <b>${value}</b>`;
      timing.append(item);
    }
    empty.hidden = report.result.emptyMessage === null;
    empty.textContent = report.result.emptyMessage ?? '';
    renderHits(report.result.hits);
    if (runtime.store.lastSearchDebug) {
      evidence.hidden = false;
      sqlText.textContent = runtime.store.lastSearchDebug.sql;
      engineSqlText.textContent = '';
    }
    if (diagOn) renderDiag({ device: runtime.requested.device, dtype: runtime.requested.dtype });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    showBanner(`检索失败：${message}`);
  } finally {
    setBusy(false);
  }
}

// ---- 事件 ----
addKbBtn.addEventListener('click', () => {
  const id = newKb.value.trim();
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(id)) {
    showBanner('知识库 id 只能用字母、数字、下划线、连字符，长度 1..64。');
    return;
  }
  kbId = id;
  newKb.value = '';
  if (!Array.from(kbSelect.options).some((o) => o.value === id)) {
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = `${id}（0 段）`;
    kbSelect.append(opt);
  }
  kbSelect.value = id;
  clearBanner();
});

kbSelect.addEventListener('change', () => {
  kbId = kbSelect.value;
  results.replaceChildren();
  timing.textContent = '';
  empty.hidden = true;
  evidence.hidden = true;
});

importBtn.addEventListener('click', () => void doImport());
searchBtn.addEventListener('click', () => void doSearch());
queryInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') void doSearch();
});
engineSqlBtn.addEventListener('click', async () => {
  if (!runtime) return;
  try {
    const { sql, engineSql } = await runtime.store.executedSqlLast();
    sqlText.textContent = sql;
    engineSqlText.textContent = engineSql;
  } catch (err) {
    showBanner(`取执行 SQL 失败：${err instanceof Error ? err.message : String(err)}`);
  }
});
exportDiag.addEventListener('click', () => {
  if (!runtime) return;
  const blob = new Blob([diagText.textContent ?? '{}'], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `diag-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
});

void boot();
