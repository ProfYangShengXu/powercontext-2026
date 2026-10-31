/**
 * 本机 Node 薄桥 —— 纯 adapter 的 HTTP 边界。
 *
 * 允许：参数绑定、协议转换、连接管理、输入形状校验。
 * 禁止：排序、融合、二次过滤、top-k 截断。
 *   → 检索结果按 store 返回的顺序原样写回；本文件没有 sort/slice/reduce 拼结果。
 */
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import type { StorePort, StoredHit } from '../ports/index.js';
import type { SearchRequest } from '../ports/index.js';

export interface SearchDebugCapable {
  searchDebug(request: SearchRequest): Promise<{ hits: StoredHit[]; sql: string; tookMs: number }>;
  engineSql(request: SearchRequest): Promise<{ sql: string; engineSql: string }>;
  databaseVersion(): Promise<string>;
}

export interface BridgeOptions {
  host: string;
  port: number;
  /** 单次请求体上限。8 段 × 512 维 ≈ 40KB，64MB 是故障隔离而不是限制。 */
  maxBodyBytes: number;
}

const DEFAULTS: BridgeOptions = { host: '127.0.0.1', port: 8787, maxBodyBytes: 64 * 1024 * 1024 };
const COLLECTION = /^[A-Za-z0-9_-]{1,64}$/;

interface WireHit {
  id: string;
  collection: string;
  text: string;
  score: number;
  metadata: Record<string, unknown>;
}

function sendJson(res: ServerResponse, status: number, payload: unknown): void {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
    'access-control-allow-origin': '*',
    'access-control-allow-headers': 'content-type',
    'access-control-allow-methods': 'GET,POST,OPTIONS',
    'cache-control': 'no-store',
  });
  res.end(body);
}

function readJson(req: IncomingMessage, maxBytes: number): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    req.on('data', (chunk: Buffer) => {
      size += chunk.length;
      if (size > maxBytes) {
        reject(Object.assign(new Error(`请求体超过 ${maxBytes} 字节上限`), { status: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      const text = Buffer.concat(chunks).toString('utf8').trim();
      if (!text) return resolve({});
      try {
        const parsed = JSON.parse(text);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
          return reject(Object.assign(new Error('请求体必须是 JSON 对象'), { status: 400 }));
        }
        resolve(parsed as Record<string, unknown>);
      } catch {
        reject(Object.assign(new Error('请求体不是合法 JSON'), { status: 400 }));
      }
    });
    req.on('error', reject);
  });
}

function toWire(hit: StoredHit): WireHit {
  // 列名/字段名映射，不改顺序、不丢行。
  return {
    id: hit.id,
    collection: hit.collection,
    text: hit.text,
    score: hit.score,
    metadata: hit.metadata,
  };
}

function parseSearchRequest(body: Record<string, unknown>): SearchRequest {
  const collection = body.collection;
  const query = body.text;
  const topK = body.topK;
  const vector = body.vector;
  if (typeof collection !== 'string' || !COLLECTION.test(collection)) {
    throw Object.assign(new Error('缺少或非法的 collection'), { status: 400 });
  }
  if (typeof query !== 'string' || !query.trim()) {
    throw Object.assign(new Error('缺少查询文本 text'), { status: 400 });
  }
  if (typeof topK !== 'number' || !Number.isInteger(topK) || topK < 1 || topK > 100) {
    throw Object.assign(new Error('topK 必须是 1..100 的整数'), { status: 400 });
  }
  if (!Array.isArray(vector) || vector.length === 0) {
    throw Object.assign(new Error('缺少查询向量 vector'), { status: 400 });
  }
  const floats: number[] = [];
  for (let i = 0; i < vector.length; i++) {
    const n = vector[i];
    if (typeof n !== 'number' || !Number.isFinite(n)) {
      throw Object.assign(new Error(`查询向量第 ${i} 个元素不是有限数`), { status: 400 });
    }
    floats.push(n);
  }
  return { query, vector: new Float32Array(floats), topK, collection };
}

function parseIngest(body: Record<string, unknown>): Array<{
  id: string;
  collection: string;
  text: string;
  vector: Float32Array;
  metadata: Record<string, string | number | boolean>;
}> {
  const raw = body.chunks;
  if (!Array.isArray(raw) || raw.length === 0) {
    throw Object.assign(new Error('chunks 必须是非空数组'), { status: 400 });
  }
  if (raw.length > 64) {
    throw Object.assign(new Error('单次最多 64 段，请分批'), { status: 400 });
  }
  return raw.map((item, index) => {
    const c = item as Record<string, unknown>;
    if (!c || typeof c !== 'object') {
      throw Object.assign(new Error(`第 ${index} 段不是对象`), { status: 400 });
    }
    if (typeof c.id !== 'string' || typeof c.collection !== 'string' || typeof c.text !== 'string') {
      throw Object.assign(new Error(`第 ${index} 段缺少 id/collection/text`), { status: 400 });
    }
    if (!Array.isArray(c.vector) || c.vector.length === 0) {
      throw Object.assign(new Error(`第 ${index} 段缺少 vector`), { status: 400 });
    }
    for (let i = 0; i < c.vector.length; i++) {
      const n = c.vector[i];
      if (typeof n !== 'number' || !Number.isFinite(n)) {
        throw Object.assign(new Error(`第 ${index} 段向量第 ${i} 个元素不是有限数`), {
          status: 400,
        });
      }
    }
    const metadata = (c.metadata ?? {}) as Record<string, string | number | boolean>;
    return {
      id: c.id,
      collection: c.collection,
      text: c.text,
      vector: new Float32Array(c.vector as number[]),
      metadata,
    };
  });
}

export function startBridge(
  store: StorePort & Partial<SearchDebugCapable>,
  options: Partial<BridgeOptions> = {},
): Promise<{ url: string; close: () => Promise<void> }> {
  const opts: BridgeOptions = { ...DEFAULTS, ...options };
  const startedAt = Date.now();

  const server = createServer(async (req, res) => {
    const started = performance.now();
    const url = new URL(req.url ?? '/', `http://${req.headers.host ?? '127.0.0.1'}`);
    const route = `${req.method} ${url.pathname}`;
    try {
      if (req.method === 'OPTIONS') return sendJson(res, 204, {});
      if (route === 'GET /api/health' || route === 'GET /api/overview') {
        const overview = await store.overview();
        const body = {
          ok: true,
          database: 'seekdb',
          databaseVersion: store.databaseVersion ? await store.databaseVersion() : null,
          uptimeSeconds: Math.round((Date.now() - startedAt) / 1000),
          ...overview,
        };
        return sendJson(res, 200, body);
      }
      if (route === 'POST /api/ingest') {
        const body = await readJson(req, opts.maxBodyBytes);
        const chunks = parseIngest(body);
        const result = await store.put(chunks);
        return sendJson(res, 200, { written: result.written });
      }
      if (route === 'POST /api/publish') {
        await readJson(req, opts.maxBodyBytes);
        const report = await store.publish();
        return sendJson(res, 200, report);
      }
      if (route === 'POST /api/search') {
        const body = await readJson(req, opts.maxBodyBytes);
        const request = parseSearchRequest(body);
        if (body.debug === true && store.searchDebug) {
          const debug = await store.searchDebug(request);
          return sendJson(res, 200, {
            hits: debug.hits.map(toWire),
            tookMs: debug.tookMs,
            debug: { sql: debug.sql, rows: debug.hits.length },
          });
        }
        const hits = await store.search(request);
        return sendJson(res, 200, { hits: hits.map(toWire), tookMs: null });
      }
      if (route === 'POST /api/executed-sql') {
        const body = await readJson(req, opts.maxBodyBytes);
        const request = parseSearchRequest(body);
        if (!store.engineSql) {
          throw Object.assign(new Error('当前适配器不支持 executed-sql'), { status: 501 });
        }
        return sendJson(res, 200, await store.engineSql(request));
      }
      return sendJson(res, 404, { error: `没有这个路由：${route}` });
    } catch (err) {
      const e = err as Error & { status?: number; detail?: unknown };
      const status = typeof e.status === 'number' ? e.status : 500;
      const payload = { error: e.message || '未知错误', ...(e.detail ? { detail: e.detail } : {}) };
      if (status >= 500) console.error(`[bridge] ${route} ${status}`, e);
      return sendJson(res, status, payload);
    } finally {
      const ms = Math.round((performance.now() - started) * 10) / 10;
      if (url.pathname !== '/api/health') {
        console.log(`[bridge] ${route} ${ms}ms`);
      }
    }
  });

  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(opts.port, opts.host, () => {
      const url = `http://${opts.host}:${opts.port}`;
      console.log(`[bridge] listening on ${url} (seekdb 薄桥：只做参数绑定与协议转换)`);
      resolve({
        url,
        close: () =>
          new Promise<void>((done) => {
            server.close(() => done());
          }),
      });
    });
  });
}
