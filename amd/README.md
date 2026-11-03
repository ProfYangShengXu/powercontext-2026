# AMD 命题 · hipVS 加速 HNSW 构建 —— 阶段交付

对应平台题 `600072`。本目录是**方案 + 实测基线**，不含完整实现。

## 一句话

把 HNSW 构建里的距离计算搬到 GPU；原型在 **cuVS（NVIDIA）** 上验证，
交付按 **hipVS（AMD）** 移植 —— 两套库是同一套 C API 的两个实现
（hipVS 官方定位：*API-compatible with cuVS, drop-in replacement*）。

## 已经实测到的（真跑，不是估算）

环境：Ubuntu 20.04 · RTX 3090 24G · CUDA driver 570.133.07 · cuvs-cu12 26.2.0 · cupy 14.2

| 规模 (n × d, k=10, nq=200) | CPU exact kNN | GPU（建 + 查） | 加速 | 结果一致性 |
|---|---|---|---|---|
| 100k × 128 | 0.312 s | 0.096 s | **3.24×** | 1.000 |
| 500k × 768 | 3.492 s | 0.211 s | **16.5×** | 1.000 |

- CPU 基线是 numpy 分块 BLAS，不是朴素循环 —— 基线不弱，加速比保守。
- `agree@k = 1.0`：GPU 结果的 top-k 与 CPU 完全一致，**不是拿精度换速度**。
- 结论：**维度越高、数据越大，差距越大**（3.2× → 16.5×）。

## 没做到 / 不能声称的

```
✗ 没有在 AMD Radeon / Instinct 上跑过 —— 手上没有 AMD 卡
✗ 没有接进 seekdb 的 HNSW 构建链路（本目录只有独立基准）
✗ hipVS 官方要求 ROCm 7.2.3 + Ubuntu 22.04/24.04 + Instinct(MI300X 等)
   与本机 Ubuntu 20.04 + RTX 3090 不一致，所以本轮不碰 HIP
```

## 目录

```
ARCHITECTURE.md            端口分层（L1 度量 → L5 后端）、判据、移植清单
bench/bench_cuvs.py        可复现基准：CPU vs cuVS，参数化 n/d/k/nq
bench/results_3090.json    上面两组读数的原始输出
```

## 跑一遍

```bash
# 需要：python3.10+ / cuvs-cu12 / cupy-cuda12x / numpy / 一块 NVIDIA GPU
python3 -m venv .venv && . .venv/bin/activate
pip install --extra-index-url=https://pypi.nvidia.com cuvs-cu12 cupy-cuda12x numpy

python bench/bench_cuvs.py --n 500000 --d 768 --k 10 --nq 200 --out bench/results_local.json
```

## 下一步（若继续）

1. 在 seekdb 侧抽出 `PairwisePort`，CPU 实现先对齐基线；
2. 用 cuVS 后端替换该端口，跑同机 A/B；
3. 按 `ARCHITECTURE.md` §10 把 `cuvs::` 换成 `hipvs::`，在 AMD 机器上复测；
4. **召回差 > 2% 判为失败** —— 宁可不出加速比，也不出一份"快了但结果不对"的索引。
