# minimax/ · SDD 上下文管理（规有所依）

给 AI 开发助手用的上下文模块：任务来了，从 **seekdb** 捞出该遵守的规格、该改的代码、该跑的测试，
拼成带 `path:line:hash` 的上下文包交给 **MiniMax**；MiniMax 改代码，跑测试，进度写进
**PowerContext**；模块重启后还能读回进度接着干。

三条端口严格分开（题面三项证据必须能分别核对）：

| 端口 | 实现 | 证据 |
|---|---|---|
| `StorePort` | `adapters/seekdb_store.py`（嵌入式 seekdb，单条 SQL 混合召回） | `docs/db-queries.md`、`docs/explain.txt` |
| `StatePort` | `adapters/pc_state.py`（PowerContext Basic Memory HTTP） | `docs/state-roundtrip.log` |
| `ModelPort` | `adapters/minimax_model.py`（MiniMax-Text-01，chatcompletion_v2） | `devlog/`、`docs/MiniMax参与记录.md` |

## 六项必做 → 证据索引

| 必做项 | 怎么证明 | 证据 |
|---|---|---|
| ① 能运行 | `make init` → `make import/context/verify` → `make test` 在同一台机器从零跑通；不依赖未交付文件 | `docs/demo-run.log`、`docs/test.log` |
| ② MiniMax 实际参与 | devlog 一条 run 内串起 context_sha256 → prompt 全文 → raw_reply → patch_sha256 → applied_commit；**且** `mmctx/adapters/minimax_payload.py` 整文件由 MiniMax-Text-01 生成 | `devlog/final4.jsonl`、`evidence/`、`docs/MiniMax参与记录.md` |
| ③ 上下文正确 | 12 条来源每条带 `chunk_uid` + `path:start-end` + `content_hash`，`verify` 逐条反查；`expected_sources` 是 CLI 硬判据；改规格重导入后只出新内容 | `docs/context-s1.log`、`docs/spec-refresh.log` |
| ④ 数据库实际参与 | 上下文包每条来源都能用 `chunk_uid` 回查 `mm_doc_chunks`；EXPLAIN 出现 `TEXT RETRIEVAL SCAN` + `VECTOR INDEX ADAPTIVE SCAN` | `docs/db-queries.md`、`docs/explain.txt` |
| ⑤ 状态能复用 | `mmctx state` 写 PowerContext，**另一个进程** `mmctx resume` 读回同一 `run_id` / `applied_commit` / `todo` | `docs/state-roundtrip.log` |
| ⑥ 代码修改有效 | 同一组测试：改前 `9 failed, 6 passed`（AssertionError），改后 `15 passed`；补丁只改 `demo_project/shop` | `docs/test-before.log`、`docs/test-after.log`、`docs/code.diff` |

## 快速开始（Linux x86_64 / WSL2，Python 3.12）

```bash
cd minimax
cp .env.example .env          # 填 MINIMAX_API_KEY；WSL 下 MMCTX_SEEKDB_PATH 用 /mnt/wsl/...
make init                     # 建 venv、装依赖、建 seekdb 表/索引（PowerContext 没起也能成功，只报 ready=false）

# 终端 A：起 PowerContext Server（Basic Memory，不需要额外模型 API）
POWERCONTEXT_HOME=/mnt/wsl/pc-home make serve

# 终端 B：
make import                   # 46 块进 seekdb；每个文件登记为 PowerContext Source
make context                  # 组装 out/s1-order-filter.{json,md}，并校验 expected_sources
make explain                  # 打印候选 SQL 的 EXPLAIN
make test                     # mmctx 自身 37 个测试（含 seekdb / PowerContext 集成）
make state                    # 任务状态写进 PowerContext（≤8192 UTF-8 字节）
make resume                   # 新进程读回状态（run_id / applied_commit / todo）
make verify                   # 来源反查 + 按 devlog 重放 final4 的补丁
```

`make demo` 会一次跑完 `test-before → import → context → run → test-after → state → resume → verify`，
但**它假定 `demo_project` 还是改前状态**。交付分支已经包含修复提交，想现场重放补丁：

```bash
git revert --no-edit ad24f11f6      # 把 filters.py 退回占位实现（新增一个 revert 提交）
make RUN_ID=replay-$(date +%s) demo # 会真实调用 MiniMax，成功后自动提交并留下 devlog
```

## 分层与装配

```
bootstrap/main.py   ← 唯一装配点（读 .env、构造三个适配器、注入 core）
   ├── adapters/    seekdb_store / pc_state / minimax_model / minimax_payload / gitrepo / embedding
   ├── ports/       StorePort / StatePort / ModelPort（互不合并）
   └── core/        切块、hash、RRF 融合、必需来源、预算、补丁守卫、状态形状（零适配器 import）
```

机器判据：`make check`（`mmctx check`）扫描 `ports/` 与 `core/`，出现
`pylibseekdb` / `pymysql` / `httpx` / `SeekdbStore(` 等即失败。当前 `ok: true`。

## 目录

```
mmctx/            CLI + 端口 + 适配器 + core
corpus/specs/     4 条规格（S-01..S-04），规格按 ## 条目切块
demo_project/     被修改的工程（stdlib + pytest），改动前 filters.py 是占位实现
samples/          s1-order-filter：task.yaml（含 expected_sources）/ reset.sh / expected.md
tests/            37 个测试：切块/融合/补丁守卫/状态/seekdb 集成/PowerContext 集成/CLI
evidence/         MiniMax 生成 module 的请求体、响应原文、落盘文件（3 轮）
devlog/           每次运行的 JSONL：context/prompt/reply/patch/self_check/apply/test/state/resume/verify
docs/             运行说明、测试日志、diff、数据库与状态证据、技术说明
```

## 关键设计（与题面攻击面对应）

- **规格换代**：`chunk_uid = sha256(doc_id:序号:content_hash)[:32]`；重导入时同一 `doc_id` 的旧块
  在同一事务里置 `is_current=0`，检索 SQL 硬带 `is_current=1`。改回原内容会复用原 uid（见 `docs/spec-refresh.log`）。
- **来源可反查**：打包含 `content_hash`；`verify` 检查路径存在、行号合法、`sha256(文件切片)==hash`、
  切片与包内正文逐字节相等、`chunk_uid` 在库里 `is_current=1`。
- **历史快照**：上下文包记录 `base_commit`，事后 `verify` 用 `git show <base_commit>:<path>` 反查，
  所以补丁改了文件也不会把旧包判成「造假」；工作区脏时退化为按工作区校验。
- **补丁守卫**：只允许 `demo_project/shop/**`，触及 tests / pytest.ini / conftest 直接拒收；
  commit 只提交守卫允许的精确路径；应用后**先跑目标测试**，失败回滚并带测试输出重试（最多 4 次）。
- **弱模型兜底**：模型可以给完整新文件（`patch_source=full_file`），由 `difflib` 生成 unified diff；
  也接受它自己写的 diff。两条路都要过语法检查 + `git apply --check`。
- **状态上限**：8192 UTF-8 字节在 `pc_state.check_text_limit()`（NFC+strip）内部校验；
  CLI 超限时按 todo→title 顺序裁剪并写 `truncated: true`，不静默丢。
- **失败不降级**：退出码 3 来源/必需来源失败、4 模型或补丁失败、5 规格已过期、6 无状态、
  7 状态超限、8 seekdb 不可用、9 PowerContext 不可达；关键失败都写 devlog。

## 环境注意

- seekdb 数据目录必须是本地 Linux 文件系统。WSL 下用 `/mnt/wsl/mmctx-seekdb`，**不要**放 `/mnt/d`（9p 上打不开）。
- 中文全文索引优先 `IK`，不可用时降级 `NGRAM`/`SPACE` 并把实际 parser 记进 `mm_meta`（`docs/db-queries.md` 可见）。
- `.env` 已被 `.gitignore` 忽略；仓库里没有任何 key、日志或响应体包含 key。
