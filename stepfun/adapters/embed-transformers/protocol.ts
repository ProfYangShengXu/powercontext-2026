/** Worker 与主线程之间的消息契约（仅类型，无运行时依赖）。 */

export type ComputeDevice = 'webgpu' | 'wasm';
export type ModelPrecision = 'fp32' | 'fp16' | 'int8' | 'uint8' | 'q8';

export interface InitRequest {
  type: 'init';
  modelId: string;
  /** 相对站点根目录的模型目录，例如 /models/；末尾必须带 /。 */
  localModelPath: string;
  /** 是否允许回源 HuggingFace（默认 false：模型同源托管）。 */
  allowRemoteModels: boolean;
  device: ComputeDevice;
  dtype: ModelPrecision;
  pooling: 'cls' | 'mean';
}

export interface EmbedRequest {
  type: 'embed';
  jobId: number;
  texts: string[];
}

export type WorkerRequest = InitRequest | EmbedRequest;

export interface InitReady {
  type: 'ready';
  device: ComputeDevice;
  dtype: ModelPrecision;
  modelId: string;
  dim: number;
  loadMs: number;
  /** 尝试 WebGPU 失败后自动降级时，这里记录原因；正常为空。 */
  fallbackReason: string | null;
}

export interface LoadProgress {
  type: 'progress';
  file: string;
  progress: number;
}

export interface EmbedDone {
  type: 'embedded';
  jobId: number;
  count: number;
  dim: number;
  ms: number;
  /** 所有向量首尾相接的 Float32Array buffer，transfer 过来，零拷贝。 */
  buffer: ArrayBuffer;
}

export interface WorkerFailure {
  type: 'error';
  phase: 'init' | 'embed';
  jobId?: number;
  message: string;
}

export type WorkerResponse = InitReady | LoadProgress | EmbedDone | WorkerFailure;
