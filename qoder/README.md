# 课引 CourseCite — Qoder 企业命题交付

> 一句话：把一学期的课程讲义丢进 **seekdb**，用一句话问「上次讲 HNSW 是在第几讲」，
> 返回 **原文段落 + 可核对出处（课程 / 讲次 / 小节 / 页码）**，并把这次检索写进
> `retrieval_log`——评审 `SELECT` 一下就知道它真查过。
>
> 本文件是评审唯一入口。代码在 `coursecite/`，Qoder Sites 静态产物在 `site/`，
> 两者不共享进程，只在仓库里交汇。

---

## 0. 现在到哪一步

| 项 | 状态 | 证据 |
|---|---|---|
| 入库 → 检索 → 引用定位（LiveStore 真查 seekdb server） | **已跑通** | §3.1 的真实输出 |
| 两个适配器实现同一 `StorePort`（Live / Snapshot） | **已跑通** | `src/coursecite/ports/store.py`、`adapters/` |
| 快照存已排好序的最终结果，前端不重排 | **已保证** | `adapters/snapshot_store.py`；`tests/test_snapshot_store.py` |
| 检索留痕写进 seekdb | **已跑通** | §2.6 `SELECT retrieval_log` |
| 单元/契约测试 | **29 passed + 2 skipped** | `pytest -q`；设 `COURSECITE_LIVE=1` 后 31 passed |
| **Qoder 开发记录（trailer + qoder-log）** | **待人工** | §5.1，审计脚本当前 exit 1 |
| **Qoder Sites 发布 + 公开访问** | **待人工** | §5.2，需要 Qoder 账号 |
| **账号 UID** | **待人工** | §5.3，提交表单要填 |

> 这台机器上没有登录 Qoder 账号，最后三件无法由代码代做；其余已按下面命令实测。

---

## 1. 它是什么

- **入库**：扫描 `data/samples/` 下的 `.md/.txt`，按 `##` 标题层级切段，记录
  课程/讲次/小节/页码/原文行号；chunk id 由内容 hash 决定，重复入库幂等。
- **检索**：查询词在 **seekdb 库内一条混合检索**里完成「全文 `MATCH...AGAINST` +
  向量 `cosine_distance` + 结构化过滤」，用 RRF 融合后排序返回——不是应用层拼的。
- **引用定位**：每条结果带 `course / lecture / section / page / path:line`，
  可点回原文那一段；没有页码就显示 `p.—`，不编造。
- **留痕**：每次 `search` 往 seekdb 的 `retrieval_log` 插一行（query、过滤、top_ids、耗时）。
- **两种部署形态**：本地/评审机用 `LiveStore` 真查 seekdb；线上静态站用
  `SnapshotStore` 回放预生成 JSON。**core 与 UI 都不知道当前是哪种。**

---

## 2. 15 分钟跑起来

### 2.0 前置

- Windows 10/11 + Docker Desktop（或 WSL2 + Docker）。
- Python **3.11+**（实测 3.12.3）。
- 首次运行 embedding 模型会下载约 **90 MB**（all-MiniLM-L6-v2，384 维），之后离线。
- **Windows 上 pyseekdb 没有 embedded 模式**（`pylibseekdb` 只有 Linux/macOS 轮子），
  **必须**连 seekdb server。别用 `SEEKDB_PATH=`。

### 2.1 起 seekdb（server 模式，评审同款）

PowerShell 或 bash，在仓库根目录：

```bash
cd qoder/coursecite
docker compose up -d
docker compose ps
```

预期（`STATUS` 含 `Up`，端口有 2881）：

```text
NAME                IMAGE                    STATUS         PORTS
coursecite-seekdb   oceanbase/seekdb:latest  Up 10 seconds  0.0.0.0:2881->2881/tcp, 0.0.0.0:2886->2886/tcp
```

- `2881` 是 **SQL 端口**，程序只连这个。
- `2886` 是 obshell 管理台（HTTP），可选，不参与检索。
- 不想用 compose 时等价命令：`docker run -d --name coursecite-seekdb -p 2881:2881 oceanbase/seekdb:latest`。

### 2.2 安装依赖

```bash
cd qoder/coursecite
python -m venv ../.venv
../.venv/bin/python -m pip install -e '.[live,dev]'      # Windows: ..\.venv\Scripts\python -m pip install -e ".[live,dev]"
```

装完做一次 seekdb 冒烟（不碰项目代码，只验证服务端能混合检索）：

```bash
cd ..                              # 回到 qoder/
.venv/bin/python tools/seekdb_smoke.py --host 127.0.0.1 --port 2881
```

预期末尾：`count=3 target=server 127.0.0.1:2881/test`，退出码 0。

### 2.3 指向库（三个环境变量）

bash：

```bash
export COURSECITE_STORE=live
export SEEKDB_HOST=127.0.0.1
export SEEKDB_PORT=2881
```

PowerShell：

```powershell
$env:COURSECITE_STORE="live"; $env:SEEKDB_HOST="127.0.0.1"; $env:SEEKDB_PORT="2881"
```

可选：`SEEKDB_USER`（默认 root）、`SEEKDB_PASSWORD`（默认空）、`SEEKDB_DATABASE`（默认 test）、
`COURSECITE_EMBED_CACHE`（模型缓存目录，默认 `~/.cache/pyseekdb`）。

### 2.4 入库

```bash
../.venv/bin/coursecite ingest
```

预期输出（退出码 0）：

```text
入库完成：文件 22，段落 66，写入 66，重复跳过 0，库内总计 66
课程：CSC3170, CSC4180, CSC5210
```

再跑一次同一条命令，**库内总计仍是 66**（upsert 幂等）；`写入 66` 是本次提交的批次数，
不是“新增 66 行”。

### 2.5 检索 + 出处

```bash
../.venv/bin/coursecite search "上次讲 HNSW 是在第几讲" --limit 3 --show-sql
```

预期（节选，退出码 0）：

```text
查询：上次讲 HNSW 是在第几讲    （live，1093 ms）

[1] CSC5210 · 第 3 讲 · 3.1 HNSW 的构造与查询 · p.17
    通道 hybrid   score 0.0318   原文 CSC5210/L03-近似最近邻.md:2 (p.17)
    HNSW 是分层可导航小世界图，上层稀疏用于快速跳转，下层稠密用于精确收敛。……

-- 实际发给 seekdb 的混合检索 SQL --
SELECT ... match(`document`) against('...' in natural language mode) ...
       cosine_distance(`embedding`, '[...]') ... ORDER BY `_distance` APPROXIMATE LIMIT 10 ...
```

`--show-sql` 打印的是**真正发给 seekdb 的 SQL**（通过追踪 pyseekdb 执行入口拿到），
能直接看到全文臂、向量臂、RRF 融合和 `APPROXIMATE`。

### 2.6 检索留痕（评审那条 SELECT）

```bash
../.venv/bin/coursecite log --limit 5
```

或直接进容器查表：

```bash
docker exec coursecite-seekdb mysql -h127.0.0.1 -P2881 -uroot -Dtest \
  -e "SELECT id,created_at,query_text,elapsed_ms,store_mode FROM retrieval_log ORDER BY id DESC LIMIT 5;"
```

预期（每跑一次 `search` 多一行）：

```text
id  created_at                  query_text            elapsed_ms  store_mode
18  2026-10-31T17:55:55+00:00   HNSW 的召回率由什么权衡   1667        live
17  2026-10-31T17:55:55+00:00   RRF 的 k 取多少          1523        live
```

### 2.7 Web UI

```bash
../.venv/bin/coursecite serve --port 8765
```

打开 <http://127.0.0.1:8765>。页面会显示当前是 `LiveStore · 真查 seekdb` 还是
`SnapshotStore · 快照回放`；检索结果按后端返回顺序渲染，前端**没有**排序逻辑。

> 同一进程只加载一次 ONNX 模型；不要同时开第二个 `serve`/`ingest`。

### 2.8 快照 + 静态站（Qoder Sites 的输入）

```bash
# 从真库导出 5 条查询的真实结果（同时刷新 data/expected/）
COURSECITE_STORE=live ../.venv/bin/coursecite snapshot

# 打包成纯静态目录 qoder/site/
../.venv/bin/python tools/build_site.py
```

只回放快照、不连库（评审没装 Docker 时的降级路径，**标注为降级、不算跑通 Live**）：

```bash
COURSECITE_STORE=snapshot ../.venv/bin/coursecite serve --port 8765
```

本地验证静态站（必须用 HTTP，`file://` 下 fetch 会被拦）：

```bash
python -m http.server 8000 --directory ../site
```

---

## 3. 验证样例

### 3.1 五条演示查询的期望首条（`data/queries.json` + `data/expected/`）

| id | 查询 | 首条出处 | 原文 |
|---|---|---|---|
| q1 | 上次讲 HNSW 是在第几讲 | CSC5210 · 第 3 讲 · 3.1 · p.17 | `CSC5210/L03-近似最近邻.md:2` |
| q2 | MVCC 是怎么实现快照隔离的 | CSC4180 · 第 4 讲 · 4.2 · p.28 | `CSC4180/L04-事务与隔离.md:6` |
| q3 | B+ 树为什么适合做数据库索引 | CSC4180 · 第 2 讲 · 2.1 · p.9 | `CSC4180/L02-索引结构.md:2` |
| q4 | 动态规划的状态转移怎么确定 | CSC3170 · 第 6 讲 · 6.1 · p.41 | `CSC3170/L06-动态规划.md:2` |
| q5 | RRF 的 k 取多少 | CSC5210 · 第 4 讲 · 4.1 · p.25 | `CSC5210/L04-混合检索与融合.md:2` |

`pytest tests/test_expected.py` 会逐条核对：快照前 8 条 id 与 `expected/` 完全一致，
且每条查询首条命中就是上表的出处。默认 `pytest -q` 是 **29 passed + 2 skipped**
（2 条真库用例默认跳过）；连上 seekdb 后：

```bash
COURSECITE_LIVE=1 SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2881 \
  ../.venv/bin/pytest tests/test_live_integration.py -v     # 2 passed
```

这两条会真的 `ingest→search→retrieval_log`，并把「从真库重新导出的快照」与仓库里
冻结的 `data/snapshots/snapshots.json` 逐条比对（chunk_id / score / citation），
补上「snapshot 只在自己生成的工件里自洽」的缺口。

### 3.2 本次实测记录（2026-10-31 ~ 11-01，WSL2 + Docker seekdb 2881）

```text
$ coursecite ingest
入库完成：文件 22，段落 66，写入 66，重复跳过 0，库内总计 66      # exit 0

$ coursecite ingest          # 第二次
入库完成：文件 22，段落 66，写入 66，重复跳过 0，库内总计 66      # 幂等

$ coursecite search "上次讲 HNSW 是在第几讲" --limit 3 --show-sql
[1] CSC5210 · 第 3 讲 · 3.1 HNSW 的构造与查询 · p.17             # exit 0

$ coursecite log --limit 1
最近 1 条检索记录：... [live] '上次讲 HNSW 是在第几讲' ... 1093ms
```

快照可复现性：从**真库**重新导出 5 条查询，与仓库里 `data/snapshots/snapshots.json`
逐条比对 **chunk_id / score / citation 全部相同**。也就是说线上回放的顺序不是另算的，
就是本地 seekdb 的真实结果。

### 3.3 失败显式（不静默降级）

```bash
COURSECITE_STORE=live SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2882 coursecite search "HNSW"
```

预期：退出码 **2**，stderr 打印可复制的修复命令（起 Docker / 设置 `SEEKDB_HOST` /
切快照），**不会**返回空结果或偷偷用本地文件兜底。

其他边界：

- `COURSECITE_STORE=snapshot coursecite ingest` → 退出码 2，明确“快照是只读回放”。
- 快照里没有的查询 → 退出码 3，并列出 5 条可回放查询，不返回空。
- Windows 上不设 `SEEKDB_HOST` → 明确报“Windows 没有 embedded，请用 Docker server”。

---

## 4. 架构：一个端口，两个适配器

```
bootstrap/main.py            唯一装配点：读环境 → 选 LiveStore 或 SnapshotStore
      │
      ├── adapters/live_store.py      ── pyseekdb hybrid_search → seekdb 2881
      └── adapters/snapshot_store.py  ── 读 data/snapshots/snapshots.json
                    │
                    ▼
             ports/store.py（StorePort，core 侧定义）
                    ▲
             core/service.py + core/retrieval.py（检索规则、引用渲染）
```

### 4.1 关键不变量（都有机器检查）

| 判据 | 怎么查 | 落点 |
|---|---|---|
| A 换适配器不改 core/ports 的 import | `tests/test_layering.py` 禁止 `pyseekdb/fastapi/adapters/snapshot` 出现在 core/ports | `ports/store.py` |
| B 新增第三种存储只加文件 + 装配点一行 | `bootstrap/main.py` 是唯一 `if store == ...` 的地方 | `bootstrap/main.py` |
| C 前端不重排 | 前端目录不出现 `rrf/recall/fusion/weight_sum`，JS 不出现 `.sort(` | `tests/test_layering.py` |
| D top-k 只定义一处 | `core/policy.py::DEFAULT_TOP_K`；适配器只用 `Query.limit` | `tests/test_layering.py` |
| E 顺序由存储侧给 | `SnapshotStore.search` 原样返回 `results`；`ensure_ordered` 断言 rank 连续 | `adapters/snapshot_store.py` |

**为什么快照必须存“已排好序的最终结果”**：如果线上前端自己算 RRF/加权，
改一次检索策略就要改两处，两处必然漂移——线上展示的结果和本地验证的结果会悄悄变成两个东西。
所以 `export_snapshot.py` 只调用真实 `LiveStore.search()`，把 `Hit` 列表原样落盘；
`SnapshotStore` 只做 `hits[:limit]` 前缀截断，**不排序、不重算分数**。

### 4.2 检索链路（LiveStore）

1. `SearchService.search()` 构造 `Query`（空查询直接 `ValueError`）。
2. `LiveStore.search()` 调 `collection.hybrid_search(query=全文臂, knn=向量臂,
   rank=RRF, n_results=limit)`；全文词由 `_fulltext_terms()` 去标点、保留 CJK/字母数字。
3. `_to_hits()` 按返回顺序编号 rank=1..N，从 metadata 还原出处。
4. `SearchService` 把 top_ids / 耗时写 `retrieval_log`，再返回。

> 已知实现细节：`retrieval_log` 的写入与 SQL 追踪走 pyseekdb 的私有执行入口
> （`_client._server`）。它集中在 `live_store.py` 的 `_select()` / `_install_trace()` 两处，
> 升级 pyseekdb 时只改这里；这是有意收窄的适配点，不是散落各处的 hack。

---

## 5. 还差哪一步才能提交（三件只有账号持有人能做）

### 5.1 Qoder 开发记录（代码写得再对，没这个也不得分）

**现状**：`python coursecite/tools/check_qoder_log.py` 当前 **exit 1**：

```text
[FAIL] 审计未通过：
  - commit b8355d93e 改动了 src/ 但没有 Qoder-Quest trailer，也不是 manual:（或未在 index.md 登记）
  - .../docs/qoder-log/index.md 里没有任何 Quest ID
```

**要做**（在 Qoder 客户端里，人来做）：

1. 逐个任务发 Quest（骨架 / 入库 / 混合检索 SQL / Web UI / 快照导出），
   prompt 末尾写验收条件（例如「`pytest tests/test_ingest.py` 全绿」）。
2. 在 **Qoder 内置终端** commit，带机械证据 trailer：

   ```text
   Qoder-Quest: <Quest ID>
   Qoder-Model: <model>
   ```

3. 建开发记录索引（两个位置脚本都认）：
   `qoder/coursecite/docs/qoder-log/index.md` 或 `qoder/docs/qoder-log/index.md`，
   表头：`序号 | 日期 | Quest ID | 目标 | prompt 摘要 | 产物 commit | 验证方式`。
4. 把现有的人工 commit `b8355d93e` 登记进 `index.md` 的「人工改动」表
   （脚本看到 hash 就按人工改动放行）。
5. 重跑 `python tools/check_qoder_log.py`，期望 `[PASS] 三档判据全绿。`

截图/录屏存 `docs/qoder-log/NN-<date>-<task>/`：任务输入框 + Quest ID、diff 视图、
Qoder 终端测试输出、`git commit` 结果。会话能否导出未知，**以 commit trailer 为主证据**。

### 5.2 Qoder Sites 发布（网站必须未登录可开）

**现状**：`qoder/site/` 已是可直接发布的静态目录
（`index.html` + `static/` + `data/snapshots.json`），本地 HTTP 验证可加载快照。

**要做**：

1. 个人账号登录 Qoder CN → 左侧栏「站点 → 添加站点」→ 输入 `@sites`。
2. prompt 写清：项目简介、痛点、核心功能、首屏三句话、仓库链接、运行说明摘要、
   **「在线体验为快照，真实运行见仓库」**声明。
3. 发布 `qoder/site` → 状态「已发布」→ 复制网址。
4. **分享 → 更改访问范围 → 任何人都可访问 → 允许公开访问**（个人版需二次确认）。
5. 无痕窗口打开线上页，截含地址栏的图；四张截图进 `qoder-log/site/`
   （站点面板 / 发布详情+网址 / 访问范围 / 无痕打开）。

注意：Free 版 Sites **只支持静态部署**，无数据库/存储；JSON 按普通静态资源随目录发布。
若平台不接受 `data/` 文件，把快照 JSON 内联进 HTML/JS 即可——只改 `site/` 产物，
不动 `src/`，也不影响架构判据。**改完内容必须重新点发布**，预览 ≠ 线上。

### 5.3 账号 UID

Qoder `设置 → 个人资料 → 编辑` 里复制 UID（提交表单要填），截图存
`docs/qoder-log/account/uid.png`。

### 5.4 提交前 checklist

- [ ] `docker compose up -d` 后，`ingest → search → log` 三条命令都有真实输出。
- [ ] `SELECT id,query_text,top_ids FROM retrieval_log` 能看到行。
- [ ] `pytest -q` 全绿（29 passed + 2 skipped；`COURSECITE_LIVE=1` 时 31 passed）。
- [ ] `python tools/check_qoder_log.py` 退出码 0。
- [ ] 无痕窗口能打开 Sites 公开页。
- [ ] `git status` 干净；`.venv/`、`.home/` 没入库。
- [ ] `grep -riE "sk-|BEGIN .*PRIVATE KEY|password\s*[:=]\s*[^\"']" qoder/` 无真凭据
      （代码只读 `SEEKDB_PASSWORD` 环境变量，默认空）。

---

## 6. 内存与运行注意（这台机器 OOM 重启过）

- all-MiniLM-L6-v2 ONNX：权重约 90 MB，**实测 `ingest` 进程峰值 RSS ≈ 740 MB**
  （含 onnxruntime 会话）。留 1.5 GB 余量比较稳。
- **同一时刻只跑一个**会加载模型的进程：`ingest` / `search` / `snapshot export` / `serve`。
  不要并发开多个终端同时跑。
- 一个进程内只创建一次 embedding 会话：`LiveStore.reset()` 复用已有
  `_embedding_function`，不会因 `--rebuild` 短暂翻倍。
- Web 是单进程；`LiveStore` 用一把 `RLock` 串行化数据库访问（pymysql 单连接不是线程安全的）。
  演示/评审够用；要真上并发得换连接池，那是另一个量级的工作。
- 快照模式不加载 ONNX，内存只有几十 MB，适合无 Docker 的评审机看页面。

---

## 7. 已知边界 / 没做什么

- 入库只收 **`.md` / `.txt`**；PDF（即使有文字层）未实现，遇到会跳过并报告。
  这是 v1 的明确边界，不假装支持。
- **编辑已入库的讲义后，旧段落不会自动消失**（chunk id 含正文/页码，upsert 只增不删）。
  需要 `coursecite ingest --rebuild` 重建整个索引；`--rebuild` 非原子（先删集合再分批写），
  **不要在 `serve` 正在服务时跑**——窗口内检索会返回空/报错。增量失效是架构里明确不做的 v1 范围。
- 快照模式只能回放 `data/queries.json` 里的 5 条查询；自由输入会明确报
  「离线快照中没有这个问题」，不会瞎编结果。
- Windows 只支持 server 模式；embedded 分支保留给 Linux/macOS，未在 Windows 承诺。
- 混合检索的 SQL 由 pyseekdb 生成，本仓库不手写 SQL；`--show-sql` 只是把实际 SQL 打出来。
- 不引第三方检索库（faiss/langchain/rank_bm25），排序与融合在 seekdb 库内完成。
- **只有成功的检索才写 `retrieval_log`**；检索抛错时 CLI/Web 会显式失败（exit 4 / 503），
  但不会补一条失败记录。`channel` 目前恒为 `hybrid`，记录不到「哪一臂命中」。
- 首次检索前若模型缓存为空，会联网下载约 90 MB（最长 600s）；离线评审机请提前预热
  `~/.cache/pyseekdb` 或设 `COURSECITE_EMBED_CACHE`。
- `/healthz` 与检索共用同一把数据库锁：DB 挂起时它也会排队（单进程演示可接受，
  真上多用户要拆 readiness 探针和连接池）。
- `retrieval_log` 只有 `CREATE TABLE IF NOT EXISTS`，没有列迁移；用干净库最稳。

---

## 8. 目录速查

```text
qoder/
├── README.md                     ← 本文件
├── coursecite/
│   ├── docker-compose.yml        ← seekdb server（2881 SQL / 2886 管理台）
│   ├── src/coursecite/
│   │   ├── ports/store.py        ← StorePort（唯一端口定义）
│   │   ├── adapters/live_store.py      ← 真查 seekdb
│   │   ├── adapters/snapshot_store.py  ← 读预生成 JSON（只截断，不重排）
│   │   ├── core/ingest.py        ← 切段 + 出处字段 + 内容 hash 幂等
│   │   ├── core/retrieval.py     ← 引用渲染 / 格式化（唯一实现）
│   │   ├── core/policy.py        ← 唯一 top-k 定义
│   │   ├── bootstrap/main.py     ← 唯一装配点
│   │   ├── cli.py / web.py       ← 两个入口，共用 SearchService
│   │   └── export.py             ← 从 LiveStore 导出快照
│   ├── data/samples/             ← 22 个自制讲义、66 段
│   ├── data/expected/            ← 5 条查询的预期结果
│   ├── data/snapshots/           ← 前端快照（已排好序）
│   ├── tests/                    ← 31 个测试（2 个真库用例默认 skip）
│   └── tools/                    ← seekdb 冒烟 / 导出 / 建站 / Qoder 审计
├── site/                         ← Qoder Sites 静态产物（发布这个目录）
└── tools/seekdb_smoke.py         ← 不依赖项目代码的 seekdb 冒烟
```
