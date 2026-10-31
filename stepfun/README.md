# 阶跃星辰 · 端侧 AI 推理与 seekdb 混合检索

一个迷你知识库 Web 应用：**文本在浏览器里变成向量** → 写入 **seekdb** → 查询时同样在浏览器里变成向量 →
seekdb 用**一条 `HYBRID_SEARCH` SQL** 完成全文 + 向量 + RRF 融合并返回排序结果。

```
浏览器（唯一做推理的地方）                     本机 Node 薄桥（只做参数绑定）
┌──────────────────────────────┐   HTTP/JSON   ┌──────────────────────────────┐
│ Web Worker: Transformers.js  │ ────────────▶ │ POST /api/ingest  写向量+原文 │
│  WebGPU+fp16 / WASM+uint8    │               │ POST /api/publish 刷新+探测   │
│  文档与查询都在这里编码       │ ◀──────────── │ POST /api/search  单条 SQL    │
└──────────────────────────────┘               └──────────────┬───────────────┘
                                                              │ seekdb-js
                                                   ┌──────────▼───────────┐
                                                   │ seekdb 1.4.0         │
                                                   │ FULLTEXT + HNSW + RRF│
                                                   └──────────────────────┘
```

- 端上推理：Transformers.js 3.8.1 + onnxruntime-web；模型 `Xenova/bge-small-zh-v1.5`（512 维）。
- 量化：WebGPU 用 **fp16**，WASM 用 **uint8**；体积、压缩比、Recall 全部实测，见下文与 `docs/tech-note.md`。
- 检索：`SELECT ... FROM HYBRID_SEARCH('kb_chunk', '<dsl>')`，融合在库内；桥不排序、不融合、不截断。

## 交付物（对应题面 4.1–4.3）

| 题面要求 | 位置 |
|---|---|
| 可运行 Web 应用 + 启动说明 | 本 README；`web/`、`bridge/` |
| 完整源码（core/ports/adapters/bootstrap 分层） | `core/`、`ports/`、`adapters/`、`bootstrap/index.ts` |
| 模型获取与量化步骤 | `scripts/fetch-model.mjs`、`scripts/measure-models.mjs`、`docs/tech-note.md` |
| seekdb 表结构与索引配置 | `adapters/store-seekdb/index.ts`（建表 DDL）、`docs/evidence/show-create-table.sql` |
| 复现测试的数据与方式 | `tests/integration.mjs`、`eval/data.json`、`eval/run-eval.mjs` |
| 一页技术说明 | `docs/tech-note.md` |
| 实测记录（含失败项） | `docs/measurement.md`、`docs/evidence/` |

## 目录

```
stepfun/
  ports/index.ts                 # EmbedPort / StorePort：稳定侧契约，零外部 import
  core/                          # 切段、用例编排、结果呈现；技术无关
    chunker.ts  knowledge-base.ts  presentation.ts
  adapters/
    embed-transformers/          # EmbedPort 的浏览器实现（Worker 内推理）
    store-http/                  # StorePort 的浏览器实现（HTTP 协议转换）
    store-seekdb/                # StorePort 的 Node 实现（单条 HYBRID_SEARCH）
  bootstrap/index.ts             # 全项目唯一装配点（模型/后端/库）
  bridge/                        # 本机 HTTP 薄桥（127.0.0.1:8787）
  web/                           # UI（Vite 根目录）
  tests/integration.mjs          # 20 条端到端断言
  eval/                          # 量化质量评测（40 段 / 20 查询 / qrels）
  scripts/                       # fetch-model / measure / doctor / evidence / check-layers
  docs/                          # 技术说明、实测记录、取证包
  demo/                          # 可直接拖进页面的示例文档
```

## 快速开始

前置：Node.js >= 20（本机实测 v22.23.2）、一个可运行的 seekdb server（1.4.0.0）、
Chrome/Edge 最新版（WebGPU 可选，不支持会自动走 WASM）。

```bash
cd stepfun
npm install            # 项目自带 .npmrc（legacy-peer-deps，绕过 seekdb 的可选 peer 元数据问题）
npm run fetch-model    # 同源准备模型：fp16 + uint8 共 68.6 MiB；有 .models/ 缓存时只做拷贝
npm run doctor         # 自检：Node/模型/ORT/seekdb/桥
```

**终端 A：启动 seekdb**

```bash
SEEKDB_BIN=/path/to/seekdb ./scripts/start-seekdb.sh
# 预期：
#   seekdb: /path/to/seekdb
#   数据目录: …/stepfun/.seekdb-data（ext4）
#   端口: 2881
#   Starting seekdb (OceanBase seekdb 1.4.0.0) …
```

取得 `seekdb` 可执行文件的三种方式：官方 `seekdb_install.sh`、源码构建
（`./build.sh release --init --make`，产物在 `build_release/src/observer/seekdb`）、或已有构建产物。

> **WSL / NTFS 注意**：seekdb 初始化会对 block file 调 `fallocate()`。数据目录若在
> WSL 的 Windows 盘挂载（v9fs/9p）或 FAT/exFAT/SMB 上，会报 `Fail to fallocate block file ... Operation not supported`
> 并退出。`start-seekdb.sh` 会先检测并给出方案（换 ext4 路径 / 用 tmpfs / 用 Docker）。
> 本项目的实测是在 tmpfs 上完成的，原始报错见 `docs/measurement.md` §1。

**终端 B：启动薄桥**

```bash
npm run bridge
# 预期：
#   [bridge] listening on http://127.0.0.1:8787 (seekdb 薄桥：只做参数绑定与协议转换)
#   [bridge] 健康检查：http://127.0.0.1:8787/api/health
curl -s http://127.0.0.1:8787/api/health
# {"ok":true,"database":"seekdb","databaseVersion":"5.7.25-OceanBase seekdb-v1.4.0.0","documents":0,...}
```

**终端 C：启动前端**

```bash
npm run dev
# 预期：Local: http://127.0.0.1:5173/
```

打开 <http://127.0.0.1:5173/>，右上角徽标会显示 `WebGPU · fp16` 或 `WASM · uint8`。
把 `demo/*.md` 拖进“选择文件”，点“导入并发布索引”，等进度 100%，再输入问题检索。

生产构建：`npm run build && npm run preview`（产物在 `web-dist/`，ORT 运行时随构建 emit 到 `dist/ort/`）。

## 怎么验证题面的硬要求

| 要求 | 验证方式（可复现） |
|---|---|
| embedding 在浏览器算 | ① 页面徽标显示真实后端；② 导入请求体里已有 `vector[512]`（Network 面板）；③ 桥的依赖/路由里没有推理（`grep -rn "transformers\|onnx" bridge/` 为空）；④ 生产构建 CSP 只允许本站 + `127.0.0.1:8787` |
| 量化 + 体积 + 质量 | `npm run measure-models`（真实 `stat` 字节数）；`npm run eval`（fp32/fp16/int8/uint8 的 Recall@5/10、nDCG@10、MRR@10） |
| WebGPU 优先、WASM 自动降级并提示 | 正常浏览器显示 `WebGPU · fp16`；`?backend=wasm` 或 `--disable-gpu` 的浏览器显示 `WASM · uint8`；初始化失败会锁 WASM 并把原因写进 banner |
| 向量/原文/元数据都在 seekdb | `SHOW CREATE TABLE kb_chunk`（`docs/evidence/show-create-table.sql`）；`SELECT COUNT(*)`、`GROUP BY kb_id` |
| 混合检索在库里完成 | `EXPLAIN` 同时出现全文臂、向量臂、`HASH FULL OUTER JOIN`、`WINDOW FUNCTION`、`TOP-N SORT`（`docs/evidence/explain*.txt`）；页面“取证”区展示单条 SQL 与引擎实际展开的 SQL |
| 增量导入 + 刷新后可检索 | 同一文件重复导入 `COUNT(*)` 不变（集成测试 [3]）；`publish()` 调 `dbms_index_manager.refresh` 并轮询到新写入样本可见才返回（实测约 0.5–0.8s） |
| 集合/会话状态 | 每次查询都带 `kb_id`，两臂都注入 `term` 过滤；集成测试 [4] 验证两个库不串 |

一条命令跑端到端断言（需要 seekdb + 桥已启动）：

```bash
npm run test:integration
# 结果：20 passed, 0 failed
```

分层体检（换模型/换后端/换库时，`core/`+`ports/` 的 import 行哈希应保持不变）：

```bash
npm run check-layers
# 9 项 PASS；同时写出 docs/evidence/layer-check.json
```

## 架构：为什么这样分层

变化频率从快到慢：浏览器能力 / 模型与量化 / seekdb DSL → 两条命题硬约束（端上推理、seekdb 检索）。
所以：

| 层 | 装什么 | 变化频率 |
|---|---|---|
| `core/` | 切段、批量编排、“无结果”文案、结果呈现 | 最慢 |
| `ports/` | `EmbedPort.encode(texts) -> vectors`、`StorePort.put/search/publish/overview` | 慢 |
| `adapters/` | Transformers.js Worker、HTTP 客户端、seekdb + SQL | 最快 |
| `bootstrap/index.ts` | **唯一**决定模型、后端、库、批大小的位置 | 随部署 |

- 端口签名里没有 `webgpu` / `dtype` / `dim` / `sql` / `rrf`（`npm run check-layers` 会报红）。
- 换实现只改适配器 + 装配点；`core/` 与 `ports/` 的 import 行逐字节不变。
- Node 薄桥是纯 adapter：只做参数绑定、协议转换、输入形状校验；不排序/融合/过滤/截断。

## 模型与量化（实测摘要）

模型 [`Xenova/bge-small-zh-v1.5`](https://huggingface.co/Xenova/bge-small-zh-v1.5)（512 维，CLS 池化，归一化）。

| dtype | 用途 | ONNX | 含 tokenizer 总计 | 压缩比 |
|---|---|---:|---:|---:|
| fp32 | 质量基线 | 90.46 MiB | 90.98 MiB | 1.00 |
| fp16 | WebGPU | 45.30 MiB | 45.82 MiB | **1.99** |
| int8 | 对照 | 22.80 MiB | 23.32 MiB | **3.90** |
| uint8 | WASM | 22.80 MiB | 23.32 MiB | **3.90** |

40 段语料 / 20 条查询 / 人工 qrels 的纯向量质量（详细数据在 `eval/results.md`）：

| dtype | Recall@5 | Recall@10 | nDCG@10 | MRR@10 |
|---|---:|---:|---:|---:|
| fp32（基线） | 0.7917 | 0.9250 | 0.8383 | 0.8521 |
| fp16 | 0.7917 | 0.9250 | 0.8383 | 0.8521 |
| int8 | 0.7833 | 0.9750 | 0.8522 | 0.8493 |
| uint8 | 0.7833 | 0.9750 | 0.8627 | 0.8738 |

结论：fp16 与 fp32 同分；int8/uint8 的 Recall@5 掉 0.84 个百分点，Recall@10 与 nDCG 反而略高
（量化带来的排序扰动在这套小评测集上处于噪声量级）。**评测方法与限制**见 `docs/tech-note.md`。

## 性能（本机实测，headless Edge 156）

|  | WASM · uint8 | WebGPU · fp16 |
|---|---:|---:|
| 模型加载（暖） | 5837 ms | 1610 ms |
| 查询向量化 | 32.9 ms | 250.3 ms |
| seekdb 检索 | 98.6 ms | 20.5 ms |
| 端到端 | 131.7 ms | 270.9 ms |

headless Edge 的 WebGPU 走 ANGLE/软件路径，单批推理比 WASM 慢；这些数字只用于验证链路与降级，
不代表真实独显。真实浏览器建议用页面上的计时面板重新测。详见 `docs/measurement.md` §4。

## 换模型 / 换后端

- **只换推理后端**：URL 加 `?backend=wasm`，或改 `bootstrap/index.ts` 的探测分支；
  `core/`、`ports/` 零改动。
- **换模型**：改 `bootstrap/index.ts` 的 `MODEL_SPEC.id / dim / pooling`，运行
  `npm run fetch-model`（新模型目录），并**重建表**（维度不同不能就地迁移）。
  相同维度也可能新旧向量混合，建议 `DROP TABLE kb_chunk` 后重导。
- **换库**：实现一个新的 `StorePort`，在 `bootstrap` 里替换，`core/` 不变。

## 已知限制（如实记录）

1. **seekdb 数据目录必须支持 `fallocate`**。WSL 的 Windows 盘挂载不行；换 ext4 路径、tmpfs 或 Docker。
2. **服务端 prepared 语句不能用于 `HYBRID_SEARCH`**：表名和 DSL 是解析期 literal，
   `conn.execute('... HYBRID_SEARCH(?, ?)')` 会 1064。桥用客户端转义后内联（仍是参数绑定，不含排序逻辑）。
3. **DSL 里的查询文本不能含 `'` 或 `\`**：引擎内部把它拼进 `MATCH ... AGAINST('...')` 时不转义。
   适配器在进全文臂前做一次字符消毒（空格替换），向量臂不受影响。
4. **`refresh` 返回成功 ≠ 立即可检索**：HNSW 有约 0.1–0.3s 的异步可见窗口；
   `publish()` 会轮询到刚写入的样本确实出现才返回。
5. **JSON 点路径过滤不可用**：`{"term":{"metadata.kb_id":"k1"}}` 报 1054；
   `kb_id` 必须做成标量列（本项目已如此），两臂都用 `term` 过滤。
6. **headless WebGPU 的性能不代表真机**；功能验证用 `--enable-unsafe-webgpu` 完成。
7. 模型文件不提交进 Git（`.gitignore`），由 `npm run fetch-model` 从 HuggingFace 同源准备；
   首次需要网络，之后可完全离线（Cache Storage + 本地模型）。

## 提交信息

- 基线分支：`origin/2026-competition-ecosystem`（基于 `eco-local` 开发）
- 资产目录：`stepfun/`
- 本次 commit：`git -C stepfun log -1 --format='%H %s'`（平台提交时填实际 SHA）

## 许可与来源

- 模型：`Xenova/bge-small-zh-v1.5`，Apache-2.0（BAAI bge-small-zh-v1.5 的 ONNX 转换）。
- 运行时：Transformers.js 3.8.1、onnxruntime-web 1.22.0-dev、seekdb-js 1.4.0、seekdb 引擎 1.4.0.0。
- 本项目代码用于 2026 OceanBase 数据库大赛企业命题提交。
