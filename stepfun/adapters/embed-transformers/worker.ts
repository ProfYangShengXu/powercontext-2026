/**
 * Embedding Worker —— 唯一的端上推理位置。
 *
 * 这里才允许出现 transformers.js / WebGPU / dtype。主线程与 core 看不到这些词。
 * 降级策略（适配器内部决定，不上抛）：
 *   1. 按装配点给的 device/dtype 初始化；
 *   2. 初始化或首次推理抛错 → 锁定 WASM + uint8 重建；
 *   3. 任何一次失败都通过 message 回传，主线程 UI 必须显示降级原因。
 */
import { env, pipeline, type FeatureExtractionPipeline } from '@huggingface/transformers';
import type {
  EmbedDone,
  InitReady,
  InitRequest,
  ModelPrecision,
  WorkerRequest,
  WorkerResponse,
} from './protocol.js';

type Pipeline = FeatureExtractionPipeline;

let pipe: Pipeline | null = null;
let activeDevice: 'webgpu' | 'wasm' = 'wasm';
let activeDtype: ModelPrecision = 'uint8';
let activeDim = 0;
let pooling: 'cls' | 'mean' = 'cls';
let initPromise: Promise<void> | null = null;
let fallbackReason: string | null = null;

function send(msg: WorkerResponse): void {
  (self as unknown as Worker).postMessage(msg);
}

function describeError(err: unknown): string {
  if (err instanceof Error) return `${err.name}: ${err.message}`;
  return String(err);
}

async function loadPipeline(req: InitRequest, device: 'webgpu' | 'wasm', dtype: ModelPrecision) {
  const extras: Record<string, unknown> = {
    device,
    dtype,
    progress_callback: (p: { status?: string; file?: string; progress?: number }) => {
      if (p && typeof p.progress === 'number' && p.file) {
        send({ type: 'progress', file: p.file, progress: p.progress });
      }
    },
  };
  return pipeline('feature-extraction', req.modelId, extras as never);
}

async function init(req: InitRequest): Promise<void> {
  env.allowLocalModels = true;
  env.localModelPath = req.localModelPath;
  env.allowRemoteModels = req.allowRemoteModels;
  env.useBrowserCache = true;
  // 同源托管 ORT 运行时，避免 transformers.js v3 默认去 jsdelivr 取 wasm。
  const onnx = env.backends?.onnx as unknown as { wasm?: Record<string, unknown> } | undefined;
  if (onnx?.wasm) {
    onnx.wasm.wasmPaths = '/ort/';
    onnx.wasm.numThreads = 1;
  }
  pooling = req.pooling;

  const t0 = performance.now();
  try {
    pipe = await loadPipeline(req, req.device, req.dtype);
    activeDevice = req.device;
    activeDtype = req.dtype;
  } catch (err) {
    if (req.device !== 'webgpu') {
      fallbackReason = describeError(err);
      throw err;
    }
    // WebGPU 不可用：自动降级，并记录原因。绝不静默。
    fallbackReason = describeError(err);
    pipe = await loadPipeline(req, 'wasm', 'uint8');
    activeDevice = 'wasm';
    activeDtype = 'uint8';
  }

  // 用一条短文本探测维度（不同模型维度不同，不能写死）。
  const probe = await pipe(['维度探测'], { pooling, normalize: true });
  activeDim = probe.dims[probe.dims.length - 1] ?? 0;
  if (!activeDim) throw new Error('模型没有返回可用的向量维度');

  const ready: InitReady = {
    type: 'ready',
    device: activeDevice,
    dtype: activeDtype,
    modelId: req.modelId,
    dim: activeDim,
    loadMs: Math.round(performance.now() - t0),
    fallbackReason,
  };
  send(ready);
}

async function embed(jobId: number, texts: string[]): Promise<void> {
  if (!pipe) throw new Error('模型尚未加载完成');
  const t0 = performance.now();
  const out = await pipe(texts, { pooling, normalize: true });
  const flat = out.data as Float32Array;
  const dim = out.dims[out.dims.length - 1] ?? activeDim;
  const expected = texts.length * dim;
  if (flat.length !== expected) {
    throw new Error(`模型输出维度异常：${flat.length} != ${texts.length} × ${dim}`);
  }
  // 复制到独立 buffer 再 transfer：out.data 可能只是大 buffer 的视图。
  const copy = new Float32Array(flat);
  const msg: EmbedDone = {
    type: 'embedded',
    jobId,
    count: texts.length,
    dim,
    ms: Math.round(performance.now() - t0),
    buffer: copy.buffer,
  };
  (self as unknown as Worker).postMessage(msg, [copy.buffer]);
}

self.onmessage = (event: MessageEvent<WorkerRequest>) => {
  const msg = event.data;
  if (msg.type === 'init') {
    if (!initPromise) {
      initPromise = init(msg).catch((err) => {
        initPromise = null;
        send({ type: 'error', phase: 'init', message: describeError(err) });
        throw err;
      });
    }
    return;
  }
  if (msg.type === 'embed') {
    void (async () => {
      try {
        if (initPromise) await initPromise;
        await embed(msg.jobId, msg.texts);
      } catch (err) {
        send({ type: 'error', phase: 'embed', jobId: msg.jobId, message: describeError(err) });
      }
    })();
  }
};
