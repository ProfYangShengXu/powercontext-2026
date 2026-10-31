/**
 * 浏览器侧 StorePort 实现：把请求序列化后发给本机 Node 薄桥。
 *
 * 它只做协议转换：字段改名、Float32Array → number[]、HTTP 状态 → Error。
 * 不排序、不融合、不过滤、不截断。返回顺序 = 桥返回顺序 = SQL 顺序。
 */
import type {
  ChunkToStore,
  CollectionSummary,
  PublishReport,
  SearchRequest,
  StoreOverview,
  StorePort,
  StoredHit,
} from '../../ports/index.js';

interface WireChunk {
  id: string;
  collection: string;
  text: string;
  vector: number[];
  metadata: Record<string, string | number | boolean>;
}

interface WireHit {
  id: string;
  collection: string;
  text: string;
  score: number;
  metadata: Record<string, unknown>;
}

interface SearchResponse {
  hits: WireHit[];
  tookMs: number;
  debug?: { sql: string; rows: number };
}

export class BridgeHttpError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = 'BridgeHttpError';
  }
}

export class HttpStoreAdapter implements StorePort {
  /** 最近一次查询请求（诊断用；不参与检索）。 */
  public lastRequest: SearchRequest | null = null;
  /** 最近一次查询桥实际执行的 SQL（诊断用）。 */
  public lastSearchDebug: { sql: string; rows: number } | null = null;

  constructor(private readonly baseUrl: string) {}

  async put(chunks: readonly ChunkToStore[]): Promise<{ written: number }> {
    const body = { chunks: chunks.map<WireChunk>((c) => this.toWire(c)) };
    const res = await this.post<{ written: number }>('/api/ingest', body);
    return { written: res.written };
  }

  async search(request: SearchRequest): Promise<readonly StoredHit[]> {
    this.lastRequest = request;
    const res = await this.post<SearchResponse>('/api/search', {
      text: request.query,
      vector: Array.from(request.vector),
      topK: request.topK,
      collection: request.collection,
      debug: true,
    });
    this.lastSearchDebug = res.debug ?? null;
    // 只做字段映射，顺序原样保留。
    return res.hits.map<StoredHit>((h) => ({
      id: h.id,
      collection: h.collection,
      text: h.text,
      score: h.score,
      metadata: h.metadata ?? {},
    }));
  }

  async publish(): Promise<PublishReport> {
    return this.post<PublishReport>('/api/publish', {});
  }

  async overview(): Promise<StoreOverview> {
    const res = await this.get<{
      documents: number;
      collections: CollectionSummary[];
      lastPublishedAt: string | null;
    }>('/api/overview');
    return {
      documents: res.documents,
      collections: res.collections,
      lastPublishedAt: res.lastPublishedAt,
    };
  }

  /** 诊断用：取回我们发出的 SQL 与引擎实际展开执行的 SQL（不影响检索结果）。 */
  async executedSqlLast(): Promise<{ sql: string; engineSql: string }> {
    if (!this.lastRequest) throw new Error('还没有执行过查询');
    return this.post<{ sql: string; engineSql: string }>('/api/executed-sql', {
      text: this.lastRequest.query,
      vector: Array.from(this.lastRequest.vector),
      topK: this.lastRequest.topK,
      collection: this.lastRequest.collection,
    });
  }

  private toWire(c: ChunkToStore): WireChunk {
    return {
      id: c.id,
      collection: c.collection,
      text: c.text,
      // 保留 6 位小数：512 维 × 8 字节文本，一批 8 条约 30KB。
      vector: Array.from(c.vector, (x) => Number(x.toFixed(6))),
      metadata: c.metadata,
    };
  }

  private async get<T>(path: string): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, { headers: { accept: 'application/json' } });
    return this.unwrap<T>(res);
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    return this.unwrap<T>(res);
  }

  private async unwrap<T>(res: Response): Promise<T> {
    const text = await res.text();
    let parsed: unknown = null;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      parsed = { error: text.slice(0, 400) };
    }
    if (!res.ok) {
      const e = parsed as { error?: string; detail?: unknown } | null;
      throw new BridgeHttpError(e?.error ?? `桥返回 HTTP ${res.status}`, res.status, e?.detail);
    }
    return parsed as T;
  }
}
