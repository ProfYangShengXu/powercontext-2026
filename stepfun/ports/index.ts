/**
 * ports/ —— 稳定侧的唯一外部契约。
 *
 * 规则（可机器检查，scripts/check-layers.mjs）：
 *   1. 本目录不 import 任何外部包（只能相对 import type）。
 *   2. 签名里不出现实现技术词：webgpu / wasm / onnx / transformers /
 *      seekdb / mysql / sql / dtype / hnsw / rrf / 维度常量。
 *
 * 端口只描述「核心需要什么能力」，不描述「用什么实现」。
 */

/** 文本进，向量出。embedding 在哪台机器上算，端口不关心。 */
export interface EmbedPort {
  encode(texts: readonly string[]): Promise<readonly Float32Array[]>;
}

/** 一条待入库的段落。vector 由 EmbedPort 产出，原样交给 StorePort。 */
export interface ChunkToStore {
  id: string;
  collection: string;
  text: string;
  vector: Float32Array;
  metadata: Record<string, string | number | boolean>;
}

export interface SearchRequest {
  /** 原始查询文本：全文那一路需要它。 */
  query: string;
  /** 查询向量：语义那一路需要它。 */
  vector: Float32Array;
  /** 用户要看的条数；截断由存储侧执行，调用方不得再截。 */
  topK: number;
  collection: string;
}

/** 一条命中。score 与顺序都由存储侧给出，core 只负责呈现。 */
export interface StoredHit {
  id: string;
  collection: string;
  text: string;
  score: number;
  metadata: Record<string, unknown>;
}

export interface CollectionSummary {
  id: string;
  documents: number;
}

export interface StoreOverview {
  documents: number;
  collections: readonly CollectionSummary[];
  lastPublishedAt: string | null;
}

export interface PublishReport {
  publishedAt: string;
  documents: number;
  /** 从 publish 开始到「新写入的样本确实可被检索」的毫秒数。 */
  readyAfterMs: number;
  /** 非致命问题（例如某版本没有 refresh 包），必须原样显示给用户。 */
  warning?: string;
}

/**
 * 写入 / 检索 / 发布。三个动作的语义：
 *   put(chunks)     幂等 upsert；同一 id 重复写不产生第二行。
 *   search(request) 返回已排序结果，调用方必须原样使用。
 *   publish()       让已写入内容可被检索；返回时应当已经可检索。
 */
export interface StorePort {
  put(chunks: readonly ChunkToStore[]): Promise<{ written: number }>;
  search(request: SearchRequest): Promise<readonly StoredHit[]>;
  publish(): Promise<PublishReport>;
  overview(): Promise<StoreOverview>;
}
