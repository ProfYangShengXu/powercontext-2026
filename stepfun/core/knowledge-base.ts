/**
 * core/knowledge-base.ts —— 用例编排。这是「规则」所在，只依赖 ports。
 *
 * 它决定：
 *   - 导入时按多大一批向量化（批大小是吞吐/内存的取舍，属业务策略）
 *   - 先写库、再 publish，publish 成功后才算导入完成
 *   - 查询时把 topK 收敛到允许区间
 *   - 结果怎么呈现
 *
 * 它不知道：模型叫什么、跑在 GPU 还是 CPU、库在哪、SQL 长什么样。
 */
import type { EmbedPort, StorePort } from '../ports/index.js';
import type { SourceDocument } from './chunker.js';
import { planChunks } from './chunker.js';
import { presentHits, type PresentedResult } from './presentation.js';

export interface KnowledgeBaseOptions {
  /** 一批送进模型多少段。太大内存尖峰，太小吞吐差。 */
  batchSize: number;
  maxTopK: number;
}

export const DEFAULT_OPTIONS: KnowledgeBaseOptions = { batchSize: 8, maxTopK: 50 };

export interface ImportHooks {
  onBatch?(done: number, total: number, batchMs: number): void;
  onStage?(stage: 'embed' | 'write' | 'publish'): void;
}

export interface ImportReport {
  chunks: number;
  documents: number;
  publishedAt: string;
  readyAfterMs: number;
}

export interface AskReport {
  result: PresentedResult;
  /** 查询向量化耗时；不参与排序，只用于性能面板。 */
  embedMs: number;
  searchMs: number;
  totalMs: number;
}

export class KnowledgeBase {
  private readonly options: KnowledgeBaseOptions;

  constructor(
    private readonly embed: EmbedPort,
    private readonly store: StorePort,
    options: Partial<KnowledgeBaseOptions> = {},
  ) {
    this.options = { ...DEFAULT_OPTIONS, ...options };
  }

  async importDocuments(
    collection: string,
    docs: readonly SourceDocument[],
    hooks: ImportHooks = {},
  ): Promise<ImportReport> {
    if (!collection.trim()) throw new Error('缺少知识库标识');
    if (!docs.length) throw new Error('没有可导入的文档');

    const drafts = planChunks(collection, docs);
    if (!drafts.length) throw new Error('文档内容为空，切段后没有可用段落');

    let done = 0;
    for (let i = 0; i < drafts.length; i += this.options.batchSize) {
      const batch = drafts.slice(i, i + this.options.batchSize);
      hooks.onStage?.('embed');
      const started = performance.now();
      const vectors = await this.embed.encode(batch.map((d) => d.text));
      if (vectors.length !== batch.length) {
        throw new Error(`向量数量与段落数量不一致：${vectors.length} != ${batch.length}`);
      }
      for (const v of vectors) {
        if (!(v instanceof Float32Array) || v.length === 0) throw new Error('模型返回了空向量');
      }
      hooks.onStage?.('write');
      await this.store.put(batch.map((d, j) => ({ ...d, vector: vectors[j] as Float32Array })));
      done += batch.length;
      hooks.onBatch?.(done, drafts.length, Math.round(performance.now() - started));
    }

    hooks.onStage?.('publish');
    const published = await this.store.publish();
    const sources = new Set(docs.map((d) => d.name));
    return {
      chunks: drafts.length,
      documents: sources.size,
      publishedAt: published.publishedAt,
      readyAfterMs: published.readyAfterMs,
    };
  }

  async ask(collection: string, query: string, topK: number): Promise<AskReport> {
    const text = query.trim();
    if (!collection.trim()) throw new Error('缺少知识库标识');
    if (!text) throw new Error('查询不能为空');
    const k = Math.min(Math.max(Math.trunc(topK) || 1, 1), this.options.maxTopK);

    const t0 = performance.now();
    const vectors = await this.embed.encode([text]);
    const embedMs = performance.now() - t0;
    const vector = vectors[0];
    if (!vector || vector.length === 0) throw new Error('查询向量为空');

    const t1 = performance.now();
    const hits = await this.store.search({ query: text, vector, topK: k, collection });
    const searchMs = performance.now() - t1;

    return {
      result: presentHits(hits, text),
      embedMs,
      searchMs,
      totalMs: performance.now() - t0,
    };
  }
}
