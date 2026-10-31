# 技术说明（一页）

## 1 模型与量化

- **模型**：[`Xenova/bge-small-zh-v1.5`](https://huggingface.co/Xenova/bge-small-zh-v1.5)，512 维，CLS 池化，`normalize=true`。
  上游为 BAAI `bge-small-zh-v1.5`（Apache-2.0）的 ONNX 转换；HF 仓库同时提供 fp32 / fp16 / int8 / uint8 四个权重文件。
- **为什么选它**：题目语料是中文；bge-small-zh 是中文小模型里端侧体积与效果较平衡的一档，
  且四个量化变体齐全，可以直接对比「同一模型的不同精度」，不用自己跑量化工具链。
- **量化方式（INT8 / FP16）**：使用 HF 仓库中**预量化**的 ONNX 变体，不在应用里做运行时量化。
  WebGPU 用 `model_fp16.onnx`；WASM 用 `model_uint8.onnx`（uint8 量化 + 反量化在 ORT 内部完成，权重只在端上）。
  Transformers.js 的 `dtype` 直接映射到文件名后缀（`fp16` → `_fp16.onnx`，`uint8` → `_uint8.onnx`）。
- **体积（`fs.statSync().size` 实测，口径 = ONNX + config/tokenizer/tokenizer_config/vocab）**：

| dtype | ONNX | 含 tokenizer | 压缩比 = fp32 / 本行 |
|---|---:|---:|---:|
| fp32（基线） | 90.46 MiB | 90.98 MiB | 1.00 |
| fp16 | 45.30 MiB | 45.82 MiB | **1.99** |
| int8 | 22.80 MiB | 23.32 MiB | **3.90** |
| uint8 | 22.80 MiB | 23.32 MiB | **3.90** |

复测：`npm run measure-models`（原始 JSON：`docs/evidence/model-sizes.json`）。

- **检索质量变化**：40 段语料 / 20 条查询 / 人工 qrels，纯向量 cosine 排序取 top-10，
  计算 Recall@5、Recall@10、nDCG@10、MRR@10（20 条查询算术平均）：

| dtype | R@5 | R@10 | nDCG@10 | MRR@10 | ΔR@5 vs fp32 |
|---|---:|---:|---:|---:|---:|
| fp32 | 0.7917 | 0.9250 | 0.8383 | 0.8521 | — |
| fp16 | 0.7917 | 0.9250 | 0.8383 | 0.8521 | 0.00 |
| int8 | 0.7833 | 0.9750 | 0.8522 | 0.8493 | −0.0084 |
| uint8 | 0.7833 | 0.9750 | 0.8627 | 0.8738 | −0.0084 |

结论：fp16 与 fp32 完全同分；int8/uint8 的 Recall@5 掉 0.84 个百分点，Recall@10 与 nDCG 略升。
**限制**：评测只隔离嵌入质量，不叠加全文臂；20 条查询的 0.84pp 差额接近评测噪声，
不能外推成「量化一定更好」，只能说「在这套小评测集上没有可观测的质量损失」。
脚本：`eval/run-eval.mjs`；数据：`eval/data.json`；结果：`eval/results.md`。

## 2 端侧运行与降级

- **运行时**：`@huggingface/transformers` 3.8.1 + `onnxruntime-web`（WebGPU EP / WASM EP）。
- **推理位置**：Module Worker（`adapters/embed-transformers/worker.ts`）。主线程只发消息；
  结果以 `Float32Array` 的 `buffer` 作为 Transferable 回传，避免结构化克隆复制 512 维向量。
- **WebGPU 选择**：`bootstrap/probeCompute()` 先 `navigator.gpu.requestAdapter()`，
  再检查 `adapter.features.has('shader-f16')`。两个条件都满足才选 `webgpu + fp16`；
  否则直接 `wasm + uint8`。只判断 `navigator.gpu` 存在是不够的（adapter 可能为 `null`）。
- **自动降级**：Worker 初始化时 `pipeline(..., {device:'webgpu'})` 抛错 → 立即用
  `wasm + uint8` 重建，并把 `fallbackReason` 回传主线程；界面 banner 显示原因，徽标锁定 WASM。
  `?backend=wasm` 是给评审验证降级路径的显式覆盖，仍走同一个装配点。
- **明确提示**：徽标常驻显示 `WebGPU · fp16` / `WASM · uint8`；`?diag=1` 额外显示
  `probe.adapterLabel`（实测 `intel / xe-lpg`）、模型加载耗时、查询向量维度与校验和、请求日志。
- **同源资产**：模型在 `/models/`，ORT wasm 在 `/ort/`（dev 由 Vite 插件从 node_modules 直出，
  build 时 emit 到 `dist/ort/`）。页面 CSP 为 `default-src 'self'`，`connect-src` 只允许本站与
  `http://127.0.0.1:8787`，`script-src` 含 `'wasm-unsafe-eval'`。因此任何 CDN/HF 出站请求都会被拦掉。
- **实测**：headless Edge 156 下两个后端都跑通完整流程（`docs/evidence/browser-*.json`）：
  `WASM · uint8`（`requestAdapter()` 返回 null）与 `WebGPU · fp16`（`intel / xe-lpg`）。

## 3 seekdb 使用

表（`adapters/store-seekdb/index.ts` 的 `init()`；实测见 `docs/evidence/show-create-table.sql`）：

```sql
CREATE TABLE kb_chunk (
  id VARCHAR(120) PRIMARY KEY,
  kb_id VARCHAR(64) NOT NULL,
  document LONGTEXT NOT NULL,
  embedding VECTOR(512) NOT NULL,
  metadata JSON,
  source VARCHAR(255) NOT NULL,
  chunk_index INT NOT NULL,
  FULLTEXT INDEX idx_fts (document) WITH PARSER ngram,
  VECTOR INDEX idx_vec (embedding) WITH (DISTANCE=l2, TYPE=hnsw, LIB=vsag),
  KEY idx_kb (kb_id)
) ORGANIZATION = HEAP;
```

- **写入**：`INSERT ... ON DUPLICATE KEY UPDATE`（多行绑定参数）。`id = hash(kb_id, source, 段序号)`，
  同一文件重复导入是覆盖，不产生第二行。
- **混合检索（单条 SQL）**：

```sql
SELECT id, kb_id, document, metadata, _score
FROM HYBRID_SEARCH('kb_chunk', '<dsl>');
```

DSL 结构（字段名在真实实例上验证过，不是猜的）：

```json
{
  "query": { "bool": { "must":   [{ "match": { "document": "<q>" } }],
                       "filter": [{ "term": { "kb_id": "<kb>" } }] } },
  "knn":   { "field": "embedding", "k": 40, "query_vector": "[…512 维…]",
             "filter": { "term": { "kb_id": "<kb>" } } },
  "rank":  { "rrf": { "rank_window_size": 40, "rank_constant": 60 } },
  "size":  10
}
```

- **融合在库内**：`EXPLAIN` 同时出现 `TEXT RETRIEVAL SCAN` / `VECTOR INDEX SCAN`（无过滤时）
  或 `VECTOR INDEX ADAPTIVE SCAN (PRE-FILTER)`（带 kb 过滤时），顶层是
  `HASH FULL OUTER JOIN` → `WINDOW FUNCTION` → `TOP-N SORT`；`_score = Σ 1/(rank_i+60)`。
  证据：`docs/evidence/explain.txt`、`explain-no-filter.txt`、`engine-sql.sql`（引擎实际展开的 SQL）。
- **增量与刷新**：`publish()` 先 `CALL dbms_index_manager.refresh()`，再对刚写入的样本做
  knn-only `HYBRID_SEARCH` 轮询，直到它真的出现（上限 8s）才返回。实测 ready 时间 0.5–0.8s；
  `kb_meta` 表持久化 `last_published_at`，桥重启后 `/api/health` 仍能显示。
- **状态隔离**：`kb_id` 是标量列 + 普通索引，两臂都注入 `term` 过滤；缺 `kb_id` 的查询直接 400。
- **桥的边界**：`bridge/` 里没有 `.sort(`、`.slice(0,`、`HYBRID_SEARCH`、`rrf`；
  `npm run check-layers` 会逐项检查。排序与截断只有 seekdb 一处实现。

## 4 状态与优化

- **批处理**：`core/knowledge-base.ts` 固定 `batchSize=8`（512 维 × 8 条约 30KB 请求体）。
  实测 1 段导入的端到端 publish 0.5–0.8s；批量时进度按段推进。
- **候选深度**：`knn.k = clamp(topK×4, 20, 100)`，`size = topK`，`rank_window_size = max(k, topK)`；
  截断发生在 SQL 内，桥不二次截断。
- **传输**：Worker → 主线程用 Transferable；主线程 → 桥用 JSON 数组（保留 6 位小数）。
- **加载**：模型同源 + `Cache Storage`，第二次加载明显更快（WASM 5837ms → 4007ms；
  WebGPU 冷 66389ms（headless 软件路径）→ 暖 1610ms）。
- **性能（headless Edge 156，单机）**：

| 配置 | 模型加载 | 查询向量化 | seekdb 检索 | 端到端 |
|---|---:|---:|---:|---:|
| WASM · uint8 | 5837 ms | 32.9 ms | 98.6 ms | 131.7 ms |
| WebGPU · fp16 | 1610 ms | 250.3 ms | 20.5 ms | 270.9 ms |

headless 的 WebGPU 走 ANGLE/软件路径，推理慢于 WASM；本表用于证明链路与降级，
真实独显上的数字请在普通窗口用页面计时面板复测（页面会分开显示三段耗时）。

- **状态**：当前知识库由 UI 的 `kbId` 决定，每次导入/查询都显式带上；切换知识库只改过滤条件，
  不触发 DDL 或索引重建。
