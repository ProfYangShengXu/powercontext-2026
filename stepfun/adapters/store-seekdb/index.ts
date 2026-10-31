/**
 * 服务端 StorePort 实现：seekdb（server 模式，经 seekdb-js 的 SeekdbClient 接入）。
 *
 * 这里允许出现 SQL / 表 / 索引 / 融合方式——它是适配器，不是核心。
 * 唯一一条检索语句就是 HYBRID_SEARCH 表函数：全文臂、向量臂、RRF 融合、top-k
 * 全部在 seekdb 引擎里完成。本文件不做任何结果排序/融合/过滤。
 *
 * 实测约束（详见 docs/measurement.md）：
 *   - 表名参数是字符串字面量，不能写 `HYBRID_SEARCH(TABLE t, ...)`；
 *   - 表名/DSL 不能走服务端 prepared 占位符，只能作为 SQL 字符串字面量传入；
 *   - DSL 里的 query 文本含单引号/反斜杠会破坏引擎内部生成的 MATCH 语句，
 *     所以进全文臂前要做一次字符消毒（不影响向量臂，也不改排序）。
 */
import { SeekdbClient } from 'seekdb';
import type {
  ChunkToStore,
  CollectionSummary,
  PublishReport,
  SearchRequest,
  StoreOverview,
  StorePort,
  StoredHit,
} from '../../ports/index.js';

export interface DbConfig {
  host: string;
  port: number;
  user: string;
  password: string;
  database: string;
}

export interface SeekdbStoreOptions {
  db: DbConfig;
  table: string;
  dim: number;
}

export interface SearchDebug {
  hits: StoredHit[];
  sql: string;
  tookMs: number;
}

const IDENT = /^[A-Za-z_][A-Za-z0-9_]*$/;
const COLLECTION = /^[A-Za-z0-9_-]{1,64}$/;
const CHUNK_ID = /^[A-Za-z0-9_-]{1,120}$/;
const MAX_TEXT_CHARS = 20000;

/** SQL 字符串字面量转义：先反斜杠，再单引号。 */
function sqlLiteral(text: string): string {
  return text.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

/**
 * 全文臂的查询文本消毒。
 * seekdb 的 HYBRID_SEARCH 会把她内部重新拼成 `MATCH(...) AGAINST('<q>')`，
 * 这一步不做转义，所以 `'` 和 `\` 必须在进入 DSL 前去掉。
 */
export function sanitizeFullTextQuery(query: string): string {
  return query.replace(/[\\']/g, ' ').replace(/\s+/g, ' ').trim();
}

export function vectorToLiteral(vector: Float32Array): string {
  let out = '[';
  for (let i = 0; i < vector.length; i++) {
    if (i) out += ',';
    out += Number(vector[i]).toFixed(6);
  }
  return out + ']';
}

function fail(message: string, detail?: unknown, status = 400): Error {
  const err = new Error(message) as Error & { detail?: unknown; status?: number };
  err.detail = detail;
  err.status = status;
  return err;
}

export class SeekdbStore implements StorePort {
  private client: SeekdbClient | null = null;
  private lastPublishedAt: string | null = null;
  private lastSample: { id: string; collection: string; vector: Float32Array } | null = null;
  private lastSearchSql = '';

  constructor(private readonly options: SeekdbStoreOptions) {
    if (!IDENT.test(options.table)) throw fail(`非法的表名：${options.table}`);
    if (!IDENT.test(options.db.database)) throw fail(`非法的库名：${options.db.database}`);
    if (!Number.isInteger(options.dim) || options.dim < 8 || options.dim > 4096) {
      throw fail(`非法的向量维度：${options.dim}`);
    }
  }

  async init(): Promise<void> {
    // 目标库可能还不存在：先用 mysql 库建库，再接目标库。
    const admin = new SeekdbClient({ ...this.options.db, database: 'mysql' });
    try {
      await admin.execute(`CREATE DATABASE IF NOT EXISTS \`${this.options.db.database}\``);
    } finally {
      await admin.close();
    }
    this.client = new SeekdbClient(this.options.db);
    const { table, dim } = this.options;
    await this.run(`CREATE TABLE IF NOT EXISTS \`${table}\` (
      id VARCHAR(120) PRIMARY KEY,
      kb_id VARCHAR(64) NOT NULL,
      document LONGTEXT NOT NULL,
      embedding VECTOR(${dim}) NOT NULL,
      metadata JSON,
      source VARCHAR(255) NOT NULL,
      chunk_index INT NOT NULL,
      FULLTEXT INDEX idx_fts (document) WITH PARSER ngram,
      VECTOR INDEX idx_vec (embedding) WITH (DISTANCE=l2, TYPE=hnsw, LIB=vsag),
      KEY idx_kb (kb_id)
    ) ORGANIZATION = HEAP`);
    await this.run(`CREATE TABLE IF NOT EXISTS kb_meta (
      k VARCHAR(64) PRIMARY KEY,
      v VARCHAR(1024) NOT NULL
    )`);
    const saved = await this.run('SELECT v FROM kb_meta WHERE k = ?', ['last_published_at']);
    const value = saved[0]?.v;
    this.lastPublishedAt = typeof value === 'string' ? value : null;
  }

  async put(chunks: readonly ChunkToStore[]): Promise<{ written: number }> {
    if (!chunks.length) return { written: 0 };
    const rows: unknown[] = [];
    const values: string[] = [];
    for (const chunk of chunks) {
      this.assertChunk(chunk);
      values.push('(?,?,?,?,?,?,?)');
      rows.push(
        chunk.id,
        chunk.collection,
        chunk.text,
        vectorToLiteral(chunk.vector),
        JSON.stringify(chunk.metadata),
        typeof chunk.metadata.source === 'string' ? chunk.metadata.source : '',
        typeof chunk.metadata.index === 'number' ? chunk.metadata.index : 0,
      );
    }
    const sql = `INSERT INTO \`${this.options.table}\`
      (id, kb_id, document, embedding, metadata, source, chunk_index)
      VALUES ${values.join(',')}
      ON DUPLICATE KEY UPDATE
        kb_id = VALUES(kb_id),
        document = VALUES(document),
        embedding = VALUES(embedding),
        metadata = VALUES(metadata),
        source = VALUES(source),
        chunk_index = VALUES(chunk_index)`;
    await this.run(sql, rows);
    const last = chunks[chunks.length - 1];
    if (last) this.lastSample = { id: last.id, collection: last.collection, vector: last.vector };
    return { written: chunks.length };
  }

  async search(request: SearchRequest): Promise<readonly StoredHit[]> {
    const { hits } = await this.searchDebug(request);
    return hits;
  }

  /** 桥的诊断入口：额外返回本次真正执行的 SQL；顺序与 hits 一致。 */
  async searchDebug(request: SearchRequest): Promise<SearchDebug> {
    this.assertSearch(request);
    const sql = this.buildSearchSql(request);
    this.lastSearchSql = sql;
    const started = performance.now();
    const rows = await this.run(sql);
    const tookMs = Math.round((performance.now() - started) * 1000) / 1000;
    // 只做列名 → 领域字段的映射；数组顺序原样保留。
    const hits: StoredHit[] = rows.map((row) => ({
      id: String(row.id),
      collection: String(row.kb_id),
      text: String(row.document),
      score: Number(row._score),
      metadata: this.parseMetadata(row.metadata),
    }));
    return { hits, sql, tookMs };
  }

  async publish(): Promise<PublishReport> {
    const started = performance.now();
    let warning: string | undefined;
    try {
      await this.run('CALL dbms_index_manager.refresh()');
    } catch (err) {
      // 旧版本没有这个包：如实记录，但仍要做可见性探测。
      warning = `refresh 调用失败：${err instanceof Error ? err.message : String(err)}`;
    }
    if (this.lastSample) {
      const ok = await this.waitSearchable(this.lastSample, 8000);
      if (!ok) {
        throw fail(
          '索引刷新后 8 秒内仍检索不到刚写入的样本。' +
            '请检查 seekdb 是否在运行、FULLTEXT/VECTOR 索引是否创建成功。',
          { sampleId: this.lastSample.id, collection: this.lastSample.collection },
        );
      }
    }
    const publishedAt = new Date().toISOString();
    this.lastPublishedAt = publishedAt;
    await this.run(
      'INSERT INTO kb_meta (k, v) VALUES (?, ?) ON DUPLICATE KEY UPDATE v = VALUES(v)',
      ['last_published_at', publishedAt],
    );
    const documents = await this.count();
    return {
      publishedAt,
      documents,
      readyAfterMs: Math.round(performance.now() - started),
      ...(warning ? { warning } : {}),
    };
  }

  async overview(): Promise<StoreOverview> {
    const rows = await this.run(
      `SELECT kb_id, COUNT(*) AS c FROM \`${this.options.table}\` GROUP BY kb_id ORDER BY kb_id`,
    );
    const collections: CollectionSummary[] = rows.map((r) => ({
      id: String(r.kb_id),
      documents: Number(r.c),
    }));
    const documents = collections.reduce((n, c) => n + c.documents, 0);
    return { documents, collections, lastPublishedAt: this.lastPublishedAt };
  }

  async databaseVersion(): Promise<string> {
    const rows = await this.run('SELECT VERSION() AS v');
    return String(rows[0]?.v ?? 'unknown');
  }

  /**
   * 取证：返回我们发出的单条 SQL，以及引擎实际展开执行的 SQL。
   * 只点按钮时才查，不进正常检索路径（正常一次查询只发一条语句）。
   */
  async engineSql(request: SearchRequest): Promise<{ sql: string; engineSql: string }> {
    this.assertSearch(request);
    const payload = sqlLiteral(this.buildDsl(request));
    const rows = await this.run(
      `SELECT DBMS_HYBRID_SEARCH.GET_SQL('${this.options.table}', '${payload}') AS s`,
    );
    return { sql: this.buildSearchSql(request), engineSql: String(rows[0]?.s ?? '') };
  }

  async close(): Promise<void> {
    await this.client?.close();
    this.client = null;
  }

  private async run(sql: string, params?: unknown[]): Promise<Record<string, unknown>[]> {
    if (!this.client) throw fail('seekdb 尚未初始化');
    const rows = params ? await this.client.execute(sql, params) : await this.client.execute(sql);
    return (rows ?? []) as unknown as Record<string, unknown>[];
  }

  private async count(): Promise<number> {
    const rows = await this.run(`SELECT COUNT(*) AS c FROM \`${this.options.table}\``);
    return Number(rows[0]?.c ?? 0);
  }

  private buildSearchSql(request: SearchRequest): string {
    return `SELECT id, kb_id, document, metadata, _score FROM HYBRID_SEARCH('${this.options.table}', '${sqlLiteral(this.buildDsl(request))}')`;
  }

  /** 单条 HYBRID_SEARCH 的 DSL：两路过滤都在库内，RRF 融合也在库内。 */
  private buildDsl(request: SearchRequest): string {
    const ftsQuery = sanitizeFullTextQuery(request.query);
    const candidateK = Math.min(Math.max(request.topK * 4, 20), 100);
    const windowSize = Math.max(candidateK, request.topK);
    const knn = {
      field: 'embedding',
      k: candidateK,
      query_vector: vectorToLiteral(request.vector),
      filter: { term: { kb_id: request.collection } },
    };
    const dsl: Record<string, unknown> = {
      knn,
      rank: { rrf: { rank_window_size: windowSize, rank_constant: 60 } },
      size: request.topK,
    };
    if (ftsQuery) {
      dsl.query = {
        bool: {
          must: [{ match: { document: ftsQuery } }],
          filter: [{ term: { kb_id: request.collection } }],
        },
      };
    }
    return JSON.stringify(dsl);
  }

  private async waitSearchable(
    sample: { id: string; collection: string; vector: Float32Array },
    timeoutMs: number,
  ): Promise<boolean> {
    const dsl = JSON.stringify({
      knn: {
        field: 'embedding',
        k: 5,
        query_vector: vectorToLiteral(sample.vector),
        filter: { term: { kb_id: sample.collection } },
      },
      size: 5,
    });
    const sql = `SELECT id FROM HYBRID_SEARCH('${this.options.table}', '${sqlLiteral(dsl)}')`;
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const rows = await this.run(sql);
      if (rows.some((r) => String(r.id) === sample.id)) return true;
      await new Promise((r) => setTimeout(r, 120));
    }
    return false;
  }

  private assertChunk(chunk: ChunkToStore): void {
    if (!CHUNK_ID.test(chunk.id)) throw fail(`段落 id 非法：${chunk.id.slice(0, 80)}`);
    if (!COLLECTION.test(chunk.collection)) throw fail(`知识库标识非法：${chunk.collection}`);
    if (!chunk.text || chunk.text.length > MAX_TEXT_CHARS) {
      throw fail(`段落正文长度非法（1..${MAX_TEXT_CHARS}）：${chunk.text?.length ?? 0}`);
    }
    if (!(chunk.vector instanceof Float32Array) || chunk.vector.length !== this.options.dim) {
      throw fail(`向量维度不符：期望 ${this.options.dim}，实际 ${chunk.vector?.length ?? 0}`, {
        id: chunk.id,
      });
    }
    for (let i = 0; i < chunk.vector.length; i++) {
      if (!Number.isFinite(chunk.vector[i])) {
        throw fail(`向量第 ${i} 个元素不是有限数`, { id: chunk.id });
      }
    }
    let meta: string;
    try {
      meta = JSON.stringify(chunk.metadata);
    } catch {
      throw fail('元数据无法序列化', { id: chunk.id });
    }
    if (meta.length > 8192) throw fail('元数据超过 8KB 上限', { id: chunk.id });
  }

  private assertSearch(request: SearchRequest): void {
    if (!COLLECTION.test(request.collection)) throw fail('缺少或非法的知识库标识');
    if (!request.query.trim()) throw fail('查询文本不能为空');
    if (!Number.isInteger(request.topK) || request.topK < 1 || request.topK > 100) {
      throw fail('topK 必须在 1..100');
    }
    if (!(request.vector instanceof Float32Array) || request.vector.length !== this.options.dim) {
      throw fail(`查询向量维度不符：期望 ${this.options.dim}，实际 ${request.vector?.length ?? 0}`);
    }
    for (let i = 0; i < request.vector.length; i++) {
      if (!Number.isFinite(request.vector[i])) throw fail('查询向量含非有限数');
    }
  }

  private parseMetadata(raw: unknown): Record<string, unknown> {
    if (raw && typeof raw === 'object') return raw as Record<string, unknown>;
    if (typeof raw === 'string') {
      try {
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {};
      } catch {
        return {};
      }
    }
    return {};
  }
}
