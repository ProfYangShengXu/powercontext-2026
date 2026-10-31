# 实测记录（含失败项）

> 所有命令都在本机真实执行；失败的命令保留原始错误，不做美化。
> 环境：WSL2 Ubuntu（Linux 6.18.33.2-microsoft-standard-WSL2，x86_64），
> Node v22.23.2，npm 10.9.8，Microsoft Edge 156.0.4314.50（Windows 侧，headless 驱动），
> GPU：Intel(R) Arc(TM) Graphics；RAM 7.4 GiB，18 vCPU。
> 运行会话的 `/tmp` 是每次命令新建的 tmpfs（bwrap），所以数据库临时目录用 `/dev/shm`，
> 见 §1.3。

## 0 三件事的结论

| # | 任务 | 结论 |
|---|---|---|
| 1 | 跑通 seekdb server | **跑通了**，但数据目录不能放 WSL 的 Windows 盘挂载（NTFS/9p，不支持 fallocate）。最终用 tmpfs + `datafile_size=512M / log_disk_size=2G` 跑起 1.4.0.0。 |
| 2 | 一条最小 SQL 验混合检索 | **通了**：`SELECT ... FROM HYBRID_SEARCH('<表名>', '<dsl>')`，返回带 `_score` 的两路融合结果；EXPLAIN 有融合节点。同时记录了 4 条真实失败（`TABLE` 关键字、JSON 点路径过滤、服务端预处理、引号/反斜杠）。 |
| 3 | 量化前后体积 | **实测**：fp32 90.98 MiB → fp16 45.82 MiB（1.99×）→ uint8 23.32 MiB（3.90×）。数字来自 `stat`，不抄文档。 |

## 1 seekdb server

### 1.1 环境与二进制

本机已有一份从源码构建好的产物（同一台机器，非本次新建）：

```
$ /path/to/seekdb --version
Starting seekdb (OceanBase seekdb 1.4.0.0) source revision 1-d0432844e70a1c8c4cb0c6b3c54eb3d8afd1c8f4.
```

连接后版本字符串：`5.7.25-OceanBase seekdb-v1.4.0.0`。

### 1.2 失败 A：数据目录在 WSL 的 Windows 盘挂载（NTFS/9p）

```bash
seekdb --base-dir WSL Windows 盘上的数据目录 --port 2881 --nodaemon \
       --parameter datafile_size=2G --parameter log_disk_size=2G
```

真实错误（`docs/evidence/raw/01-seekdb-logs-on-ntfs.txt`）：

```
EDIAG [SHARE] open_block_file (ob_io_device_helper.cpp:1195)
  Fail to fallocate block file, (ret=-4009, sys_ret=-1,
  store_path=".../store/sstable/block_file", adjust_file_size=2147483648,
  errmsg="Operation not supported")
ERROR [STORAGE_BLKMGR] ... block manager start fail
[process exit code 1]
```

对照：`fallocate -l 64M WSL Windows 盘上的探针文件` → `fallocate: Operation not supported`。
源码里 Linux 分支只有 `::fallocate`，没有 `ftruncate` 兜底（`src/share/ob_io_device_helper.cpp:1191`）。

### 1.3 失败 B：`log_disk_size` 太小

```bash
seekdb --base-dir /dev/shm/sdb2 --port 2882 --nodaemon \
       --parameter datafile_size=512M --parameter log_disk_size=1G
```

```
EDIAG [STORAGE] start (ob_server.cpp:1223) fail to initialize server runtime(ret=-4736,
  ret="OB_RESOURCE_UNIT_VALUE_INVALID")
[process exit code 1]
```

源码：`SERVER_MIN_LOG_DISK_SIZE = 2LL * GB`（`src/share/resource/ob_server_resource.h:49`），
显式传 1G 直接判非法。原始日志：`docs/evidence/raw/02-seekdb-logdisk-too-small.txt`。

### 1.4 成功：tmpfs + 合法参数

```bash
mkdir -p /dev/shm/sdb
cd /dev/shm/sdb
seekdb --base-dir /dev/shm/sdb --port 2881 --nodaemon \
       --parameter memory_limit=1G --parameter datafile_size=512M --parameter log_disk_size=2G
```

```
Starting seekdb (OceanBase seekdb 1.4.0.0) source revision 1-d0432844e70a...
Change working directory to base dir. path='/dev/shm/sdb/'
The log file is in the directory: '/dev/shm/sdb/log/'
# ss -ltn | grep 2881
LISTEN 0 1024 0.0.0.0:2881 0.0.0.0:*
```

`memory_limit=1G` 没有生效（运行时 memory_budget 仍是 6.37G，来自物理内存自动预算），
但不影响启动；真正卡住启动的是 `log_disk_size`。**结论：server 模式可跑，交付脚本里
`start-seekdb.sh` 预设 `datafile_size=512M / log_disk_size=2G`。**

> 本机 `/tmp` 是每次 bash 调用新建的 tmpfs，后台进程还会随调用结束被杀；
> 所以这里用 `/dev/shm`（tmpfs）承载数据目录，并把 server 作为长驻任务运行。
> 这是**本会话沙箱**的限制，不是应用要求；普通机器上把数据目录放 ext4/xfs 即可。

### 1.5 未做 / 跑不了

- **embedded 模式**（`@seekdb/js-bindings`）：未在本机验证。该包首次 `require` 会从 S3 下载
  53,857,927 B 的 `seekdb-js-bindings-linux-x64.zip` 到 `$HOME/.seekdb/bindings`；本会话 `$HOME` 只读，
  且 embedded 与 server 是同一个引擎、同样要 fallocate，数据目录问题不会消失。交付走 server 模式。
- **Docker 镜像**：`oceanbase/seekdb:latest` 未拉取，未验证。

## 2 一条最小 SQL 验证混合检索

### 2.1 建表（真实执行成功）

```sql
CREATE DATABASE IF NOT EXISTS kbprobe;
USE kbprobe;
CREATE TABLE kb_chunk (
  id VARCHAR(64) PRIMARY KEY,
  document TEXT,
  embedding VECTOR(8),
  metadata JSON,
  FULLTEXT INDEX idx_fts (document) WITH PARSER ngram,
  VECTOR INDEX idx_vec (embedding) WITH (DISTANCE=l2, TYPE=hnsw, LIB=vsag)
) ORGANIZATION = HEAP;
```

（先用 8 维做探针，正式应用是 512 维；建表语句在 `adapters/store-seekdb/index.ts`。）

### 2.2 通的写法（返回真实结果）

```sql
SELECT * FROM HYBRID_SEARCH('kb_chunk', '{
  "query": {"match": {"document": "alpha"}},
  "knn":   {"field": "embedding", "k": 2, "query_vector": "[0.92,0.95,…]"},
  "rank":  {"rrf": {}},
  "size":  2
}');
```

真实返回（`docs/evidence/raw/03-hybrid-probe.out`）：

```json
[{"id":"a","document":"alpha quarterly report oceanbase",
  "embedding":"[0.9207,0.9546,…]","metadata":{"kb_id":"k1"},"_score":"0.0164"}]
```

关键事实：**第一个参数是表名字符串字面量**，不是 `TABLE` 关键字；`_score` 是字符串。

### 2.3 失败记录（全部真实报错）

| 尝试 | 真实结果 | 结论 |
|---|---|---|
| `HYBRID_SEARCH(TABLE kb_chunk, '<dsl>')` | `ER_PARSE_ERROR 1064 ... near 'TABLE kb_chunk, ...'` | 文档/公开资料的 `TABLE` 写法在 seekdb 1.4.0.0 不成立 |
| `{"term":{"metadata.kb_id":"k1"}}`（query 或 knn 的 filter） | `ER_BAD_FIELD_ERROR 1054 Unknown column 'metadata.kb_id'` | JSON 点路径不能当 term 列名；`kb_id` 必须是标量列 |
| `SELECT ... FROM HYBRID_SEARCH(?, ?)`（服务端预处理） | `ER_PARSE_ERROR 1064 ... near '?, ?)'` | 表名/DSL 在 resolver 期按 literal 读取，不能走 binary protocol |
| 客户端 `query()` 绑定 `?` | **成功**，返回 3 行 | mysql2 的文本协议会先转义内联；桥必须用这种方式 |
| `query_vector` 传 JSON 数组、维度不符 | `inconsistent dimension: expected 8 got 3`（errno 7600） | 数组形式可接受，维度检查生效 |
| 查询文本含 `'` 或 `\`（如 `what's "vector"`） | `ER_PARSE_ERROR 1064 ... near 's "vector"' in natural language mode` | 引擎内部把查询文本拼进 `MATCH ... AGAINST('...')` 时不转义；适配器进 DSL 前做字符消毒（空格替换），之后成功 |
| `SELECT dbms_index_manager.refresh()` | `FUNCTION dbms_index_manager.refresh does not exist (1305)` | 它是存储过程，必须 `CALL` |
| `EXPLAIN ...` 经 seekdb-js `execute()` | 返回 `null` | SDK 只对 SELECT 返回行；EXPLAIN 用独立脚本（mysql2）取证 |

### 2.4 融合证据（EXPLAIN / 引擎实际 SQL）

无 kb 过滤时（`docs/evidence/explain-no-filter.txt`）：

```
TOP-N SORT
└─HASH FULL OUTER JOIN
  ├─SUBPLAN SCAN _fts
  │ └─WINDOW FUNCTION
  │   └─TEXT RETRIEVAL SCAN   kb_chunk(idx_fts)
  └─SUBPLAN SCAN _vs
    └─WINDOW FUNCTION
      └─VECTOR INDEX SCAN     kb_chunk(idx_vec)
```

带 kb 过滤时（`docs/evidence/explain.txt`，小表上优化器改走过滤更便宜的路径）：

```
TOP-N SORT
└─HASH FULL OUTER JOIN
  ├─WINDOW FUNCTION └─TABLE RANGE SCAN              kb_chunk(idx_kb) + filter(MATCH…)
  └─WINDOW FUNCTION └─VECTOR INDEX ADAPTIVE SCAN (PRE-FILTER) kb_chunk(idx_kb)
```

两种计划里 `HASH FULL OUTER JOIN` / `WINDOW FUNCTION` / `TOP-N SORT` 都在，`_score` 表达式是
`ifnull(1/(_keyword_rank+60),0) + ifnull(1/(_semantic_rank+60),0)`。

`SELECT DBMS_HYBRID_SEARCH.GET_SQL('kb_chunk', '<dsl>')` 能打印引擎实际展开的完整 SQL
（`docs/evidence/engine-sql.sql`，11,409 字符），页面“取证”区也能一键取回。

### 2.5 写入后可见性（这是 A7 的真实难点）

同一向量连续做 knn-only `HYBRID_SEARCH`，观察刚插入的行何时出现
（`docs/evidence/raw/05-visibility-probe.txt`）：

```
immediate t=+29ms  rows=7 targetPos=-1
+50ms    t=+84ms  rows=7 targetPos=-1
+250ms   t=+298ms rows=8 targetPos=1     ← 出现了
after-refresh t=+616ms rows=8 targetPos=1
+1s      t=+1622ms rows=8 targetPos=1
```

结论：HNSW 有约 0.1–0.3s 的异步可见窗口；`CALL dbms_index_manager.refresh()` 返回成功
**不代表**新内容已可检索。实现的 `publish()` 因此在 refresh 后轮询到样本可见才返回
（实测 517–708ms）。

## 3 模型量化前后体积

来源：HuggingFace `Xenova/bge-small-zh-v1.5`，下载后本地实测（`npm run measure-models`）。

口径：同一目录下 `onnx/<权重>` + `config.json` + `tokenizer.json` + `tokenizer_config.json` + `vocab.txt` 的字节数之和。

```
$ npm run measure-models
| dtype | 权重文件              | ONNX MiB | 含 tokenizer 总 MiB | 压缩比（fp32/本行） |
| fp32  | model.onnx            | 90.46    | 90.98               | 1                   |
| fp16  | model_fp16.onnx       | 45.30    | 45.82               | 1.99                |
| int8  | model_int8.onnx       | 22.80    | 23.32               | 3.90                |
| uint8 | model_uint8.onnx      | 22.80    | 23.32               | 3.90                |
| q8    | model_quantized.onnx  | 22.90    | 23.42               | 3.88                |
```

原始 JSON：`docs/evidence/model-sizes.json`（含每个文件的字节数）。
注意：`du -sb` 得到的是字节数，换算成 MiB 才是上表；若按 MB（10^6）会得到 90.4/45.3/22.8，
与常见资料里的「86.2/43.2/21.8 MB」对不上——那是 MiB 标成了 MB。本表统一 MiB。

质量对比见 `eval/results.md`，方法与限制见 `docs/tech-note.md` §1。

## 4 浏览器端实测

用 Edge 的 CDP 驱动真实页面，两个后端各跑一遍「加载模型 → 新建知识库 → 导入 1 段 → 检索」。

|  | WASM · uint8 | WebGPU · fp16 |
|---|---|---|
| 启动参数 | `--headless=new --disable-gpu` | `--headless=new --enable-unsafe-webgpu` |
| 徽标 | `WASM · uint8` | `WebGPU · fp16` |
| 探测结果 | `requestAdapter() 返回 null` | `intel / xe-lpg`，`shader-f16=true` |
| 模型加载 | 5837 ms（冷，含下载）/ 4007 ms（暖） | 66389 ms（冷，软件路径）/ 1610 ms（暖） |
| 导入 + publish | 1 段，708 ms | 1 段，517 ms |
| 查询向量化 | 32.9 ms | 250.3 ms |
| seekdb 检索 | 98.6 ms | 20.5 ms |
| 端到端 | 131.7 ms | 270.9 ms |

- 原始记录：`docs/evidence/browser-wasm.json`、`docs/evidence/browser-webgpu.json`。
- WebGPU 的冷加载 66s 是 headless/ANGLE 软件路径，**不能当成真机性能**；
  本项只证明「WebGPU 探测 + fp16 加载 + 完整链路」可用。
- 生产构建也在同一个 headless Edge 里验证过：`npm run build && npm run preview` 后
  徽标 `WASM · uint8`，检索正常，ORT 运行时来自 `dist/ort/`。

## 5 测试与分层检查

```
$ npm run test:integration
...
  ✓ 单条 SQL 是 HYBRID_SEARCH
  ✓ SQL 里用了 RRF
  ✓ SQL 里带 kb 过滤
  ✓ 返回顺序是分数降序（桥没有重排）
  ✓ 重复导入不增行（6 → 6）
  ✓ KB2 的查询只返回 KB2 的行
  ✓ 维度不符被拒（400）
  ✓ 引号查询不报错
结果：20 passed, 0 failed
```

原始输出：`docs/evidence/raw/06-integration-test.txt`。

```
$ npm run check-layers
PASS  core/ports 零外部 import — 4 个文件
      core+ports import 行 sha256 = 87f839368c09036ae9b068a2fe4fa254de5efe97007164292f55ca32b892a83f
PASS  core/ports 不含实现技术词
PASS  唯一装配点 — bootstrap/index.ts
PASS  Node 桥 import 图不含推理依赖 — 11 个模块
PASS  SQL/融合只出现在 store-seekdb 适配器
PASS  桥不排序/不融合/不截断
PASS  EmbedPort 有浏览器实现
PASS  StorePort 有两个实现 — browser/http + node/seekdb
PASS  源码不含本机绝对路径
分层体检：全部通过
```

评测：

```
$ npm run eval
[eval] fp32  R@5=0.7917 R@10=0.925 nDCG@10=0.8383 MRR=0.8521 (11.2ms/段)
[eval] fp16  R@5=0.7917 R@10=0.925 nDCG@10=0.8383 MRR=0.8521 (15ms/段)
[eval] int8  R@5=0.7833 R@10=0.975 nDCG@10=0.8522 MRR=0.8493 (4.8ms/段)
[eval] uint8 R@5=0.7833 R@10=0.975 nDCG@10=0.8627 MRR=0.8738 (7.4ms/段)
```

（Node CPU 的 ms/段 只用于横向比较，不是浏览器性能。）

## 6 已知问题 / 未验证

1. **真机独显 WebGPU 未测**：本机只有 Intel Arc + headless，没有可用的真实 GPU 窗口自动化环境。
   两个后端的**功能**都验证了；**性能**不代表真机。
2. **Docker / embedded 模式未验证**（见 §1.5）。
3. **Vite dev 依赖预打包**：首次启动时 `@huggingface/transformers` 需要预打包，
   否则 Worker 会拿到 504（Outdated Optimize Dep）。配置里已 `optimizeDeps.include`；
   若仍遇到，删 `node_modules/.vite` 后重启 dev。
4. **生产构建有重复 wasm**：`dist/assets/` 与 `dist/ort/` 各有一份
   `ort-wasm-simd-threaded.jsep.wasm`（21.6 MiB），功能无影响，体积偏大。
5. **WSL 的 Windows 盘挂载上 inotify 不工作**：Vite dev 已开 polling；普通磁盘无此问题。
6. **本会话的 npm 缓存**：默认 `$HOME/.npm` 只读，安装时用 `--cache <可写目录>`；
   普通机器不需要。
7. **模型文件未入库**：`.models/` 与 `web/public/models/` 被 `.gitignore` 忽略，
   评审需先 `npm run fetch-model`（约 68.6 MiB，需要网络）。

## 7 复现命令

```bash
cd stepfun
npm install
npm run fetch-model
SEEKDB_BIN=/path/to/seekdb ./scripts/start-seekdb.sh   # 终端 A
npm run bridge                                          # 终端 B
npm run dev                                             # 终端 C

npm run doctor
npm run test:integration
npm run measure-models
npm run eval
npm run check-layers
npm run evidence      # 需要库里已有数据
```
