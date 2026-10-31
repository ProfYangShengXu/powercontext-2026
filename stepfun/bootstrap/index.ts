/**
 * bootstrap/index.ts —— 全项目唯一的装配点。
 *
 * 只有这个文件决定：
 *   - 用哪个模型、哪个精度
 *   - 用哪个推理后端（探测 WebGPU，失败/不支持则 WASM）
 *   - 连哪个 seekdb（库/表/账号占位）
 *
 * 它 new 具体实现，然后把抽象（EmbedPort / StorePort）注入 core。
 * 其它任何文件出现 `new TransformersEmbedAdapter / new HttpStoreAdapter /
 * new SeekdbStore / new KnowledgeBase` 都算越权（scripts/check-layers.mjs 会红）。
 *
 * 浏览器侧与 Node 侧分开两个工厂，但仍在这一个文件里；
 * Node 侧适配器用变量路径动态 import，避免 Vite 把 mysql2 打进前端包。
 */
import { KnowledgeBase } from '../core/knowledge-base.js';
import { TransformersEmbedAdapter } from '../adapters/embed-transformers/adapter.js';
import { HttpStoreAdapter } from '../adapters/store-http/index.js';
import type { ModelPrecision } from '../adapters/embed-transformers/protocol.js';

export interface ModelSpec {
  /** 开源模型仓库 id；本地模型目录用同一路径（/models/<id>/…）。 */
  id: string;
  dim: number;
  pooling: 'cls' | 'mean';
  dtypeByDevice: { webgpu: ModelPrecision; wasm: ModelPrecision };
  localModelPath: string;
  allowRemoteModels: boolean;
}

/**
 * 选型理由：题目要求中文可检索；bge-small-zh-v1.5 是 512 维中文小模型，
 * HF 上同时提供 fp32 / fp16 / int8 / uint8 四个 ONNX 变体，适合端侧。
 * 换模型只改这里（加适配器），core/ 与 ports/ 的 import 行不变。
 */
export const MODEL_SPEC: ModelSpec = {
  id: 'Xenova/bge-small-zh-v1.5',
  dim: 512,
  pooling: 'cls',
  dtypeByDevice: { webgpu: 'fp16', wasm: 'uint8' },
  localModelPath: '/models/',
  allowRemoteModels: false,
};

export const BRIDGE_SPEC = {
  host: '127.0.0.1',
  port: 8787,
  url:
    (typeof import.meta !== 'undefined' && (import.meta as { env?: Record<string, string> }).env
      ?.VITE_BRIDGE_URL) || 'http://127.0.0.1:8787',
  maxBodyBytes: 64 * 1024 * 1024,
};

const env = (key: string, fallback: string): string =>
  typeof process !== 'undefined' && process.env?.[key] ? String(process.env[key]) : fallback;

export const STORE_SPEC = {
  table: 'kb_chunk',
  dim: MODEL_SPEC.dim,
  db: {
    host: env('SEEKDB_HOST', '127.0.0.1'),
    port: Number(env('SEEKDB_PORT', '2881')),
    user: env('SEEKDB_USER', 'root'),
    // 占位值：真实口令只通过环境变量注入，不写进任何文件。
    password: env('SEEKDB_PASSWORD', ''),
    database: env('SEEKDB_DATABASE', 'seekdb_kb'),
  },
};

export const CORE_SPEC = { batchSize: 8, maxTopK: 50 };

export interface DeviceProbe {
  webgpuAvailable: boolean;
  fp16Supported: boolean;
  adapterLabel: string;
  reason: string | null;
}

/** 探测能力，不创建任何实现；只有装配点有资格做这个决定。 */
export async function probeCompute(): Promise<DeviceProbe> {
  const gpu = (navigator as unknown as { gpu?: { requestAdapter(): Promise<unknown> } }).gpu;
  if (!gpu) {
    return { webgpuAvailable: false, fp16Supported: false, adapterLabel: 'n/a', reason: 'navigator.gpu 不存在' };
  }
  try {
    const adapter = (await gpu.requestAdapter()) as {
      features?: { has(name: string): boolean };
      info?: { vendor?: string; architecture?: string; device?: string };
    } | null;
    if (!adapter) {
      return { webgpuAvailable: false, fp16Supported: false, adapterLabel: 'n/a', reason: 'requestAdapter() 返回 null' };
    }
    const fp16 = adapter.features?.has('shader-f16') ?? false;
    const info = adapter.info;
    const label = [info?.vendor, info?.architecture, info?.device].filter(Boolean).join(' / ') || 'unknown';
    return { webgpuAvailable: true, fp16Supported: fp16, adapterLabel: label, reason: null };
  } catch (err) {
    return {
      webgpuAvailable: false,
      fp16Supported: false,
      adapterLabel: 'n/a',
      reason: err instanceof Error ? err.message : String(err),
    };
  }
}

export interface BrowserRuntime {
  kb: KnowledgeBase;
  embed: TransformersEmbedAdapter;
  store: HttpStoreAdapter;
  probe: DeviceProbe;
  requested: { device: 'webgpu' | 'wasm'; dtype: ModelPrecision };
  model: ModelSpec;
}

/**
 * 装配浏览器侧：探测 → 选 device/dtype → 注入 core。
 * `?backend=wasm` 是给评审验证降级路径用的显式覆盖（仍然经过同一个装配点）。
 */
export async function createBrowserRuntime(
  options: { bridgeUrl?: string; backendOverride?: 'webgpu' | 'wasm' } = {},
): Promise<BrowserRuntime> {
  const probe = await probeCompute();
  const override =
    options.backendOverride ??
    (typeof location !== 'undefined'
      ? (new URLSearchParams(location.search).get('backend') as 'webgpu' | 'wasm' | null)
      : null) ??
    undefined;

  // 只有「适配器存在 + 支持 shader-f16」才用 WebGPU/fp16；否则直接 WASM/uint8。
  const useWebgpu = override !== 'wasm' && probe.webgpuAvailable && probe.fp16Supported;
  const device: 'webgpu' | 'wasm' = override === 'wasm' ? 'wasm' : useWebgpu ? 'webgpu' : 'wasm';
  const dtype = MODEL_SPEC.dtypeByDevice[device];

  const embed = new TransformersEmbedAdapter({
    modelId: MODEL_SPEC.id,
    localModelPath: MODEL_SPEC.localModelPath,
    allowRemoteModels: MODEL_SPEC.allowRemoteModels,
    device,
    dtype,
    pooling: MODEL_SPEC.pooling,
  });
  const store = new HttpStoreAdapter(options.bridgeUrl ?? BRIDGE_SPEC.url);
  const kb = new KnowledgeBase(embed, store, CORE_SPEC);
  return { kb, embed, store, probe, requested: { device, dtype }, model: MODEL_SPEC };
}

export interface BridgeRuntime {
  store: {
    put: import('../ports/index.js').StorePort['put'];
    search: import('../ports/index.js').StorePort['search'];
    publish: import('../ports/index.js').StorePort['publish'];
    overview: import('../ports/index.js').StorePort['overview'];
    searchDebug: (request: import('../ports/index.js').SearchRequest) => Promise<{
      hits: import('../ports/index.js').StoredHit[];
      sql: string;
      tookMs: number;
    }>;
    engineSql: (request: import('../ports/index.js').SearchRequest) => Promise<{ sql: string; engineSql: string }>;
    databaseVersion: () => Promise<string>;
    close: () => Promise<void>;
  };
}

/** 装配 Node 侧：动态 import 让前端构建看不到 seekdb/mysql2。 */
export async function createBridgeRuntime(options: { table?: string; dim?: number } = {}): Promise<BridgeRuntime> {
  const modulePath = '../adapters/store-seekdb/index.ts';
  const mod = (await import(/* @vite-ignore */ modulePath)) as typeof import('../adapters/store-seekdb/index.js');
  const store = new mod.SeekdbStore({
    db: STORE_SPEC.db,
    table: options.table ?? STORE_SPEC.table,
    dim: options.dim ?? STORE_SPEC.dim,
  });
  await store.init();
  return { store };
}
