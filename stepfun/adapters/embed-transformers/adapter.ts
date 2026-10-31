/**
 * 浏览器侧 EmbedPort 实现：把编码任务交给 Module Worker 里的 Transformers.js。
 *
 * 为什么 Worker：模型初始化与批推理会卡住主线程（16ms/帧预算），
 * 进度条会假死。代价是诊断信息必须 postMessage 回来。
 */
import type { EmbedPort } from '../../ports/index.js';
import type {
  EmbedRequest,
  InitRequest,
  ModelPrecision,
  WorkerResponse,
} from './protocol.js';

export interface EmbedAdapterConfig {
  modelId: string;
  localModelPath: string;
  allowRemoteModels: boolean;
  device: 'webgpu' | 'wasm';
  dtype: ModelPrecision;
  pooling: 'cls' | 'mean';
}

export interface EmbedRuntimeInfo {
  device: 'webgpu' | 'wasm';
  dtype: ModelPrecision;
  modelId: string;
  dim: number;
  loadMs: number;
  fallbackReason: string | null;
}

export type LoadProgressListener = (file: string, progress: number) => void;

export class TransformersEmbedAdapter implements EmbedPort {
  private readonly worker: Worker;
  private readonly infoPromise: Promise<EmbedRuntimeInfo>;
  private info: EmbedRuntimeInfo | null = null;
  private resolveInfo!: (v: EmbedRuntimeInfo) => void;
  private rejectInfo!: (e: Error) => void;
  private nextJob = 1;
  private readonly pending = new Map<
    number,
    { resolve: (v: Float32Array[]) => void; reject: (e: Error) => void }
  >();
  private readonly progressListeners = new Set<LoadProgressListener>();
  public lastEmbedMs = 0;

  constructor(config: EmbedAdapterConfig) {
    this.worker = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' });
    this.worker.onmessage = (event: MessageEvent<WorkerResponse>) => this.onMessage(event.data);
    this.worker.onerror = (event) => {
      const where = event.filename ? ` @ ${event.filename}:${event.lineno ?? 0}` : '';
      const err = new Error(`Embedding Worker 崩溃：${event.message || 'unknown'}${where}`);
      this.rejectInfo(err);
      for (const p of this.pending.values()) p.reject(err);
      this.pending.clear();
    };
    this.worker.onmessageerror = () => {
      this.rejectInfo(new Error('Embedding Worker 消息无法反序列化'));
    };
    this.infoPromise = new Promise<EmbedRuntimeInfo>((resolve, reject) => {
      this.resolveInfo = resolve;
      this.rejectInfo = reject;
    });
    this.infoPromise.catch(() => undefined);
    const init: InitRequest = { type: 'init', ...config };
    this.worker.postMessage(init);
  }

  /** 模型加载完成（或失败）后得到真实的后端/精度/维度。 */
  ready(): Promise<EmbedRuntimeInfo> {
    return this.infoPromise;
  }

  onProgress(listener: LoadProgressListener): () => void {
    this.progressListeners.add(listener);
    return () => this.progressListeners.delete(listener);
  }

  async encode(texts: readonly string[]): Promise<readonly Float32Array[]> {
    if (texts.length === 0) return [];
    const info = this.info ?? (await this.infoPromise);
    const jobId = this.nextJob++;
    return new Promise<Float32Array[]>((resolve, reject) => {
      this.pending.set(jobId, { resolve, reject });
      const msg: EmbedRequest = { type: 'embed', jobId, texts: [...texts] };
      this.worker.postMessage(msg);
      if (!this.info) this.info = info;
    });
  }

  private onMessage(msg: WorkerResponse): void {
    if (msg.type === 'progress') {
      for (const l of this.progressListeners) l(msg.file, msg.progress);
      return;
    }
    if (msg.type === 'ready') {
      this.info = {
        device: msg.device,
        dtype: msg.dtype,
        modelId: msg.modelId,
        dim: msg.dim,
        loadMs: msg.loadMs,
        fallbackReason: msg.fallbackReason,
      };
      this.resolveInfo(this.info);
      return;
    }
    if (msg.type === 'error') {
      const err = new Error(msg.message);
      if (msg.phase === 'init') {
        this.rejectInfo(err);
      } else if (typeof msg.jobId === 'number') {
        this.pending.get(msg.jobId)?.reject(err);
        this.pending.delete(msg.jobId);
      }
      return;
    }
    if (msg.type === 'embedded') {
      const pending = this.pending.get(msg.jobId);
      if (!pending) return;
      this.pending.delete(msg.jobId);
      this.lastEmbedMs = msg.ms;
      const all = new Float32Array(msg.buffer);
      const vectors: Float32Array[] = [];
      for (let i = 0; i < msg.count; i++) {
        vectors.push(all.slice(i * msg.dim, (i + 1) * msg.dim));
      }
      pending.resolve(vectors);
    }
  }
}
