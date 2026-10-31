# 架构落地：变化频率 → 分层 → 端口 → 装配点

对应 `04-阶跃星辰-端侧检索.md` §2.5。本文只记录「代码怎么落」，不重复推导。

## 1 变化频率与切层

```
变得最快
  │  浏览器能力（WebGPU 支持程度、内核版本）
  │  模型版本与量化方案（bge-small-zh / MiniLM，fp16 / uint8）
  │  seekdb 混合检索的 DSL 细节、索引刷新策略
  │  批大小、候选深度
变化最慢
  └  两条命题硬约束：① embedding 必须在用户设备本地算 ② 存储与检索必须走 seekdb
```

| 层 | 代码 | 变化频率 | 允许 import |
|---|---|---|---|
| `core/` | `chunker.ts`、`knowledge-base.ts`、`presentation.ts` | 最慢 | 只能相对路径（零外部包） |
| `ports/` | `index.ts`（EmbedPort / StorePort） | 慢 | 同上 |
| `adapters/` | `embed-transformers/`、`store-http/`、`store-seekdb/` | 最快 | 任何 SDK |
| `bootstrap/` | `index.ts` | 随部署 | 所有实现 |

## 2 依赖方向

```
bootstrap/index.ts  ──new──▶ adapters/*
        │                         │ implements
        │                         ▼
        └──new──▶ core/ ──depends──▶ ports/
                                  ▲
                                  └── adapters 实现（箭头单向，无环）
```

- `core/` 不 import transformers / onnxruntime / seekdb / mysql（`npm run check-layers`）。
- `ports/` 的签名没有 `webgpu`、`dtype`、`dim`、`sql`、`rrf`（同上）。
- Node 桥的 import 图（从 `bridge/main.ts` 传递闭包）不含推理依赖。

## 3 端口

```ts
// ports/index.ts
interface EmbedPort {
  encode(texts: readonly string[]): Promise<readonly Float32Array[]>;
}
interface StorePort {
  put(chunks: readonly ChunkToStore[]): Promise<{ written: number }>;
  search(request: SearchRequest): Promise<readonly StoredHit[]>;
  publish(): Promise<PublishReport>;
  overview(): Promise<StoreOverview>;
}
```

| 端口 | 实现 1 | 实现 2 | 说明 |
|---|---|---|---|
| `EmbedPort` | `adapters/embed-transformers`（浏览器 Worker） | —（服务端调试版未做） | 端口定义在稳定侧，实现在浏览器 |
| `StorePort` | `adapters/store-http`（浏览器，HTTP 协议转换） | `adapters/store-seekdb`（Node，seekdb + SQL） | 桥是第二个实现的载体 |

融合方式（RRF）**不出现在端口上**：`search(query, k)` 只表达“要什么”，
`HYBRID_SEARCH` 的 DSL 与 `rank.rrf` 是 seekdb 适配器的机制（与架构文档 ADR-5 一致）。

## 4 唯一装配点

`bootstrap/index.ts` 是唯一 `new` 具体实现的地方：

```ts
createBrowserRuntime() → probeCompute() → new TransformersEmbedAdapter + new HttpStoreAdapter + new KnowledgeBase
createBridgeRuntime()  → new SeekdbStore（动态 import，避免 Vite 把 mysql2 打进前端）
```

- `MODEL_SPEC` / `STORE_SPEC` / `BRIDGE_SPEC` / `CORE_SPEC` 都只在这里定义。
- `bootstrap/index.ts` 之外出现 `new TransformersEmbedAdapter|HttpStoreAdapter|SeekdbStore|KnowledgeBase`
  ⇒ `check-layers` 报红。

## 5 机器判据（`npm run check-layers`）

```
core+ports import 行 sha256 = 87f839368c09036ae9b068a2fe4fa254de5efe97007164292f55ca32b892a83f
```

- **判据 A**（换嵌入实现 ⇒ core/ports import 不变）：脚本对 `core/`+`ports/` 的所有 import 行取 sha256；
  换模型/换后端/换库后重跑，哈希不变。实测已写入 `docs/evidence/layer-check.json`。
- **判据 B**（换 WebGPU→WASM ⇒ 只改适配器/装配点）：`?backend=wasm` 只影响 bootstrap 的选择，
  `core/`、`ports/`、UI 零改动；两个后端都实测跑通。
- **判据 C**（换模型 ⇒ core 不变）：模型 id/dim/pooling 只出现在 `bootstrap/index.ts`；
  换模型只改配置 + 适配器模型目录。

## 6 与架构文档的差异（如实记录）

1. **切段位置**：文档 §3 表格写“切段在桥进程”；实现放在浏览器的 `core/chunker.ts`（Worker 内执行）。
   原因：切段是纯字符串规则，放 core 才能被浏览器/测试共用；放 Worker 同样不阻塞主线程，
   且少一次“上传原文 → 回传段落 → 再上传向量”的往返。桥因此没有任何非 StorePort 的职责。
2. **`publish()` 代替 `refreshIndex()`**：端口用中性名；Node 适配器内部才是
   `dbms_index_manager.refresh` + 可见性轮询。
3. **`StorePort` 只有 `put` / `search` / `publish` / `overview`**：`obtain(ref)` 这类读取原文的端口
   在本用例里没有调用方，按“没用上的抽象一律删除”不保留。
4. **两个工厂在一个文件**：`createBrowserRuntime` 与 `createBridgeRuntime` 都住在
   `bootstrap/index.ts`，保证“决定模型/后端/库”只有一处；Node 适配器用变量路径动态 import，
   避免前端构建把 `seekdb`/`mysql2` 打进去。
