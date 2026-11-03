# AMD 命题 · hipVS 加速 HNSW 构建 · 架构

> 2026-11-02 · 对应平台题 `600072`（20 分，评审制）
> 定位：**原型在 cuVS/3090 上跑通，交付按 hipVS 移植** —— 依据见 §2.3

---

## 0. 人话

这题要的不是"发明新算法"，是**把 seekdb 建 HNSW 索引时那段最耗时的距离计算搬到 GPU 上**，
然后拿同机前后对比的读数证明它确实快了。

麻烦在于：**真跑的卡（AMD）我们借不到，能跑的卡（3090）用的是另一套库（cuVS）。**

架构要解决的核心问题就一个：**让"在 3090 上验证的方案"和"在 AMD 上交付的方案"是同一份代码。**

---

## 1. 目标 / 非目标

**目标**

```
① 把 HNSW 构建里的距离计算切出 GPU 后端
② 同机 A/B：CPU 构建 vs GPU 构建，出构建耗时 + 加速比
③ 代码按 cuVS/hipVS 兼容 API 写，换库只改链接与头文件
④ 留一份可复现的基准脚本 + 硬件说明
```

**非目标**

```
✗ 改 HNSW 图结构算法本身（那是研究，不是本题）
✗ 优化查询侧（那是另一题 600080 的事）
✗ 声称在 AMD 卡上实测过（没测就写没测，§9 有判据）
```

---

## 2. 事实基线（都查过，不是假设）

### 2.1 hipVS 的硬门槛

官方 `System requirements`（hipVS 1.0.0）：

```
ROCm        7.2.3
OS          Ubuntu 24.04 / 22.04
GPU         MI355X (gfx950) / MI325X·MI300X (gfx942) / MI250·MI210 (gfx90a)
Python      3.10 – 3.13
```

**只有 Instinct，没有一款 Radeon** —— 而题面写的是「AMD Radeon GPU」。题面与文档不一致，见 §10。

### 2.2 本机能跑什么

```
本机        Intel Arc 核显，无 NVIDIA / 无 AMD → 跑不了 HIP（hip-nvcc 也不行，见下）
学校服务器  Ubuntu 20.04 · 8×RTX 3090 · CUDA 10.1 · 无 ROCm
            → hipVS 的 CMake 在 project() 就报 Failed to find HIP root directory
```

### 2.3 为什么"cuVS 原型"是成立的

hipVS 官方定位：

> hipVS is **API-compatible with cuVS** — drop-in replacement for existing cuVS workflows on AMD GPUs

也就是说两套库是**同一套 C API 的两个实现**。这不是"差不多"，是设计目标本身。

```
写代码时   #include <cuvs/...>     或   <hipvs/...>（同名 API）
换库时     改头文件 + 改链接（cuvs → hipvs）
采集读数   只能在有卡的机器上做 —— 3090 上是 cuVS 的数，AMD 上是 hipVS 的数
```

**因此本架构的分级是：方案正确性在 3090 上验证，性能结论标注为"cuVS 等效读数"，AMD 读数标注【未测】。**

---

## 3. 变化频率 → 分层

按"多久会变一次"切，从稳到易变：

| 层 | 内容 | 变化频率 | 依赖方向 |
|---|---|---|---|
| **L1 度量** | 构建耗时、加速比、CV 的算法 | 几乎不变 | 谁也不依赖 |
| **L2 构建编排** | seekdb 构建入口、分块、并行度 | 很少变 | → L3 |
| **L3 索引后端端口** | `NearNeighborGraphBuilder`（CPU / GPU 两种实现） | 偶尔变 | → L4 |
| **L4 距离核** | 成对距离、top-k 选择 | 偶尔变 | → L5 |
| **L5 设备抽象** | cuVS / hipVS 的具体调用 | 最易变 | 无 |

**判据**：把 L5 从 cuVS 换成 hipVS，L1–L4 **一行不改**。

---

## 4. 端口与实现（谁负责什么）

```
                 ┌──────────────────────────────┐
                 │ L2  ob_hnsw_build_entry      │  唯一入口：seekdb 建索引时调
                 └──────────────┬───────────────┘
                                │ GraphBuildPort.build(vectors, m, ef) -> Graph
                 ┌──────────────▼───────────────┐
                 │ L3  GraphBuilder             │
                 │   ├─ CpuHnswBuilder          │  保底实现，永远可用
                 │   └─ GpuAssistedBuilder      │  本架构新增
                 └──────────────┬───────────────┘
                                │ PairwisePort.topk(queries, base, k) -> (idx, dist)
                 ┌──────────────▼───────────────┐
                 │ L4  TopkKernel               │  分块、显存预算、回退
                 └──────────────┬───────────────┘
                                │ <cuvs/neighbors/brute_force.h> 等
                 ┌──────────────▼───────────────┐
                 │ L5  cuvs_backend.cpp         │  唯一一处出现库名
                 │     hipvs_backend.cpp        │  换库时只加这个文件
                 └──────────────────────────────┘
```

**端口强弱**

```
GraphBuildPort   强端口 —— 两个实现必须产出等价图（同 m/ef 下召回差 ≤ 2%）
PairwisePort     强端口 —— 返回 top-k 的语义、距离度量、排序稳定性必须一致
TopkKernel       弱端口 —— 允许后端自己决定分块策略，只要结果等价
```

**归属**：端口定义在 `L3/L4` 的公共头里；`L5` 是唯一允许出现 `cuvs::` / `hipvs::` 命名空间的地方，
**其它任何文件出现这两个名字，分层就破了**（§9 有机器检查）。

---

## 5. 构建流程与装配点

```
① seekdb 收到建索引请求
② ob_hnsw_build_entry 组装向量矩阵（float32, [n, d]）
③ 分块：按显存预算切 block（默认 4GB 上限，见 §7.3）
④ 每个 block：
     GpuAssistedBuilder → TopkKernel.topk(...) → 得 block 内近邻
⑤ 跨 block 归并近邻（CPU，O(n·k)）
⑥ 剪枝成 HNSW 层结构（CPU，复用现有代码，不改算法）
⑦ 落盘
```

**唯一装配点**：`ob_hnsw_build_entry` 里的一行

```cpp
auto builder = cfg.use_gpu ? make_gpu_builder(cfg) : make_cpu_builder(cfg);
```

**开关**：`hnsw_build_gpu=on/off`，默认 off。**off 时行为必须与改动前逐字节一致**。

---

## 6. 数值口径（这题最容易翻车的地方）

```
计分指标     构建 wall time（秒）
对照方式     同一台机器、同一份数据、同一 m/ef、交替跑 CPU/GPU 各 3 次
报告值       中位数 + 极差，不只报最好那次
噪声判据     CV > 15% 视为无效，重跑
```

**三条铁律**

```
① 不许拿"GPU 核函数耗时"当"构建耗时" —— 那是把搬运和归并藏起来了
② 不许跨机比较 —— 学校的 3090 和任何云上 AMD 卡的数字不能相除
③ 召回必须一起报 —— 加速了但召回掉 5%，那不是加速，是偷工
```

---

## 7. 失败与降级（每条都要能落到代码）

| 情况 | 行为 | 落点 |
|---|---|---|
| 无 GPU / 驱动缺失 | 回退 `CpuHnswBuilder`，日志 `hnsw.gpu.unavailable` | L3 工厂函数 |
| cuVS 初始化失败 | 同上，但**作业失败而非静默**：记 `degraded=true` 进结果 | L4 |
| 显存不足 | 自动减半 block，最多降 3 次，再不行回退 CPU | L4 |
| 距离度量不支持 | 启动期校验，直接拒绝建索引 | L2 入口 |
| 结果召回差 > 2% | **判为失败**，不落盘，报错退出 | ⑥ 之后 |

**最后一条是这题的守门员**：宁可不出加速比，也不许出一份"快了但结果不对"的索引。

---

## 8. 依赖与禁区

```
允许依赖   cuVS（3090 原型）/ hipVS（AMD 交付）/ CUDA 或 ROCm runtime
禁止       torch / transformers / 任何深度学习框架（这题不需要模型）
禁止       把 seekdb 的 HNSW 算法替换成 cuVS CAGRA 后当"我们的实现"（那是换库不是优化）
```

---

## 9. 验收判据

| 判据 | 怎么验 | 不过的后果 |
|---|---|---|
| GPU 路径与 CPU 路径同 m/ef 下召回差 ≤ 2% | 固定查询集，算 recall@10 | 红 |
| 开关关闭时输出逐字节一致 | 同一输入两次建索引，比对文件哈希 | 红 |
| 加速比用同机交替 3 次的中位数 | 基准脚本输出原始 6 个数 | 红 |
| CV > 15% 时不许报加速比 | 脚本自动判 | 红 |
| `cuvs::` / `hipvs::` 只出现在 L5 | `grep -rn "cuvs::\|hipvs::" src/` | 红 |
| 无 GPU 时能退回 CPU 且结果正确 | 拔掉可见设备跑一遍 | 红 |

**两条反例**

```
故意把 block 设成 1 条向量         ⇒ 「加速比」必须消失（开销吃掉收益），脚本要报"无加速"
故意把召回阈值放宽到 10%           ⇒ 「召回差 ≤ 2%」必须红
```

---

## 10. 移植路径（cuVS → hipVS）

```
① 头文件     #include <cuvs/neighbors/brute_force.h>
              → <hipvs/neighbors/brute_force.h>
② 命名空间   cuvs::neighbors::brute_force::search(...)
              → hipvs::neighbors::brute_force::search(...)
③ 链接       -lcuvs_c -lcuvs   →  -lhipvs
④ 构建       CMake 增加 HIP 分支；不写死 CUDA_ARCH
⑤ 硬件要求   ROCm 7.2.3 + Ubuntu 22.04/24.04 + Instinct（题面说 Radeon，文档说没有）
```

**第 ⑤ 条要在交付材料里明写**：这是题面与官方文档的冲突，不是我们没做功课。
写上去了，评审知道我们查过；不写，评审以为我们不知道。

---

## 11. 不做

```
✗ 不改 HNSW 算法（m / ef / 剪枝规则一律不动）
✗ 不做多卡（hipVS 多卡官方标注 experimental）
✗ 不碰查询侧
✗ 不声称 AMD 实测（没卡就没测，写清楚反而加分）
```

---

## 12. 里程碑

```
M1  3090 上跑通 cuVS brute-force top-k 样例，拿到单次耗时        （半天）
M2  seekdb 里定位构建热点，抽出 PairwisePort，CPU 实现先过        （一天）
M3  GPU 实现接上，同机 A/B 出第一组数                             （一天）
M4  召回校验 + 降级路径 + 基准脚本                                （半天）
M5  移植说明 + 交付材料                                          （半天）
```

**M1 出数就停：如果 cuVS 自己的 top-k 加速比不到 2×，这题不值得往下做。**
