/**
 * core/chunker.ts —— 纯字符串切段，零外部依赖。
 *
 * 为什么在 core：切段规则（标题优先、长度上限、重叠）是「怎么把文档变成可检索单元」
 * 的业务规则，十年不变；放在哪台机器执行是部署选择，不是规则。
 *
 * 本文件被浏览器 Worker 与测试直接 import，因此不允许出现 DOM / Node API。
 */

export interface SourceDocument {
  name: string;
  text: string;
}

export interface ChunkDraft {
  id: string;
  collection: string;
  text: string;
  metadata: { source: string; index: number; chars: number };
}

export interface ChunkPolicy {
  /** 单段目标长度（字符）。BGE 的 512 token 上限对中文约等于 512 字，留余量。 */
  targetChars: number;
  /** 单段硬上限。超过则按句切开。 */
  maxChars: number;
  /** 相邻段重叠字符，避免一句话被切断后两边都检索不到。 */
  overlapChars: number;
}

export const DEFAULT_CHUNK_POLICY: ChunkPolicy = {
  targetChars: 420,
  maxChars: 600,
  overlapChars: 60,
};

/** FNV-1a 32bit，两轮拼成 64bit 十六进制。纯函数，浏览器/Node 结果一致。 */
export function stableHash(input: string): string {
  let h1 = 0x811c9dc5;
  let h2 = 0x01000193;
  for (let i = 0; i < input.length; i++) {
    const c = input.charCodeAt(i);
    h1 ^= c;
    h1 = Math.imul(h1, 0x01000193) >>> 0;
    h2 ^= c + i;
    h2 = Math.imul(h2, 0x85ebca6b) >>> 0;
  }
  return h1.toString(16).padStart(8, '0') + h2.toString(16).padStart(8, '0');
}

/** 段落 id 只由「知识库 + 来源 + 段序号」决定：重导同一文件是 upsert，不是新增。 */
export function chunkId(collection: string, source: string, index: number): string {
  return `c${stableHash(`${collection}\u0000${source}`)}-${index.toString(36)}`;
}

function normalize(text: string): string {
  return text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').replace(/\u0000/g, '').trim();
}

/** 把一段超长文本按句末标点切成 <= maxChars 的片段。 */
function splitLong(paragraph: string, maxChars: number): string[] {
  if (paragraph.length <= maxChars) return [paragraph];
  const sentences = paragraph.split(/(?<=[。！？!?；;.\n])/u);
  const out: string[] = [];
  let buf = '';
  for (const s of sentences) {
    if (!s) continue;
    if (buf.length + s.length <= maxChars) {
      buf += s;
      continue;
    }
    if (buf) out.push(buf);
    if (s.length <= maxChars) {
      buf = s;
    } else {
      // 没有标点的超长串（例如表格/代码）：硬切。
      for (let i = 0; i < s.length; i += maxChars) out.push(s.slice(i, i + maxChars));
      buf = '';
    }
  }
  if (buf) out.push(buf);
  return out;
}

/**
 * 切段：先按空行分块（标题/段落边界），再按长度打包。
 * 输出顺序稳定，同样的输入永远得到同样的 id 序列。
 */
export function planChunks(
  collection: string,
  docs: readonly SourceDocument[],
  policy: ChunkPolicy = DEFAULT_CHUNK_POLICY,
): ChunkDraft[] {
  if (!collection.trim()) throw new Error('缺少知识库标识');
  const out: ChunkDraft[] = [];
  for (const doc of docs) {
    const text = normalize(doc.text);
    if (!text) continue;
    const source = doc.name.trim() || 'untitled';
    const blocks = text.split(/\n\s*\n/).flatMap((p) => splitLong(p.trim(), policy.maxChars));

    // 第一步：按 targetChars 打包成互不重叠的正文段。
    const bodies: string[] = [];
    let buf: string[] = [];
    let bufLen = 0;
    const flush = () => {
      const body = buf.join('\n\n').trim();
      if (body) bodies.push(body);
      buf = [];
      bufLen = 0;
    };
    for (const block of blocks) {
      if (!block) continue;
      if (bufLen > 0 && bufLen + block.length + 2 > policy.targetChars) flush();
      buf.push(block);
      bufLen += block.length + 2;
      if (bufLen >= policy.targetChars) flush();
    }
    flush();

    // 第二步：给第 i>0 段加前一段的尾部作为重叠，再落 id。
    bodies.forEach((body, index) => {
      const prev = index > 0 ? bodies[index - 1] ?? '' : '';
      const overlap = prev.slice(Math.max(0, prev.length - policy.overlapChars));
      const withOverlap = overlap ? `${overlap}\n${body}` : body;
      const clipped = withOverlap.slice(0, policy.maxChars);
      out.push({
        id: chunkId(collection, source, index),
        collection,
        text: clipped,
        metadata: { source, index, chars: clipped.length },
      });
    });
  }
  return out;
}
