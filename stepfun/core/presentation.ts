/**
 * core/presentation.ts —— 检索结果怎么呈现。
 * 只做映射与文案，**不排序、不截断**：顺序与条数是存储侧的决定。
 */
import type { StoredHit } from '../ports/index.js';

export interface PresentedHit {
  rank: number;
  id: string;
  collection: string;
  score: number;
  text: string;
  metadata: Record<string, unknown>;
}

export interface PresentedResult {
  hits: PresentedHit[];
  emptyMessage: string | null;
}

export function presentHits(hits: readonly StoredHit[], query: string): PresentedResult {
  const presented = hits.map((h, i) => ({
    rank: i + 1,
    id: h.id,
    collection: h.collection,
    score: h.score,
    text: h.text,
    metadata: h.metadata,
  }));
  return {
    hits: presented,
    emptyMessage:
      presented.length === 0
        ? `没有检索到与「${query}」相关的段落。可以换一个更短的关键词，或确认索引已刷新（导入后点一次“刷新索引”）。`
        : null,
  };
}
