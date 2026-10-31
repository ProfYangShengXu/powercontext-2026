# MiniMax 参与记录

> 结论先行：本仓库有**两处**代码是 MiniMax 模型的产出，都能对上提交：
> 1. `mmctx/adapters/minimax_payload.py`（121 行，整文件）——由 `MiniMax-Text-01` 生成，3 轮调用，提交 `4fc7a68ce`；
> 2. `demo_project/shop/filters.py` 的功能实现——由 `MiniMax-Text-01` 在 `final4` 运行中一次调用生成，
>    提交 `ad24f11f6`，devlog 记录 context → prompt → reply → patch → self_check → apply → test 全链。
>
> 不是「调了一下 API」：模型输出由脚本直接写入仓库文件，提交里的字节与响应原文一致，可用 sha256 复核。

## 1. 模型写入的模块（硬要求）

- 模型：`MiniMax-Text-01`
- 端点：`https://api.minimax.chat/v1` + `/text/chatcompletion_v2`（`Authorization: Bearer <key>`）
- 任务边界：把 chatcompletion_v2 的 HTTP 响应解析成结构化结果 / 可分类错误。纯函数，无网络、无文件 IO。
- 落盘文件：`mmctx/adapters/minimax_payload.py`
- 文件 sha256：`d4c5925a847455acc8ecd2a5788148b269a6b9392fd3525633290c8a829cbb5f`
  （与 `evidence/03/generated.py` 逐字节相同）
- 提交：`4fc7a68ce3addc7e7c7f08198e51e75bd920d8db`
  （`git show 4fc7a68ce -- minimax/mmctx/adapters/minimax_payload.py`，完整副本见 `docs/minimax-module.diff`）

### 调用与修正过程（3 次）

| 轮次 | 请求体 | 响应原文 | 直接落盘 | 验收 | tokens | 耗时 |
|---|---|---|---|---|---|---|
| 01 | `evidence/01/request.json` | `evidence/01/response.json` | `evidence/01/generated.py` | 10 个契约测试 **2 失败** | 2581 | 30.9s |
| 02 | `evidence/02/request.json` | `evidence/02/response.json` | `evidence/02/generated.py` | **1 失败**（HTTP 200+非 dict body 漏判） | 2787 | 32.1s |
| 03 | `evidence/03/request.json` | `evidence/03/response.json` | `evidence/03/generated.py` | **10/10 通过** | 2739 | 40.2s |

- 提示词原文：`evidence/prompt-01.md`、`evidence/prompt-02.md`、`evidence/prompt-03.md`
  （第 2、3 轮把上一轮失败断言与上一版代码一起回灌，模型据此改对）。
- 生成脚本：`evidence/harness/generate.py`（读 `.env` 的 key，只放 `Authorization` 头；
  `request.json` 里 key 已脱敏为 `<MINIMAX_API_KEY 已省略>`；响应原文与落盘文件都由脚本写，无人工抄写）。
- 验收测试：`tests/test_minimax_payload.py`（10 个用例，`make test` 中常驻）。

### 哪几行是模型产出的

`mmctx/adapters/minimax_payload.py`（121 行，整个文件）：

| 行 | 内容 |
|---|---|
| 1–5 | `from __future__`、`dataclass` import |
| 7–14 | `ParsedResponse` 字段定义 |
| 16–25 | `PayloadError`（`code` / `retryable` / `status_code`） |
| 27–121 | `parse_response()`：HTTP 错误分类、`base_resp` 业务错误、空 choices、空 content、usage/model/request_id 回退 |

复核命令：

```bash
cd minimax
sha256sum mmctx/adapters/minimax_payload.py evidence/03/generated.py    # 两行相同
git show 4fc7a68ce -- minimax/mmctx/adapters/minimax_payload.py | head
.venv/bin/python -m pytest -q tests/test_minimax_payload.py             # 10 passed
```

## 2. 业务补丁（项②）

`make RUN_ID=final4 demo` 的真实一次调用（`devlog/final4.jsonl`）：

| 字段 | 值 |
|---|---|
| run_id | `final4` |
| 模型 / 端点 | `MiniMax-Text-01` / `https://api.minimax.chat/v1/text/chatcompletion_v2` |
| 调用次数 | 1（attempt=1 即通过自检） |
| 请求 | `devlog/final4.prompt.txt`（8567 B，sha256 见 run 记录） |
| 响应原文 | `devlog/final4.reply-1.txt` |
| 解析出的补丁 | `devlog/final4.patch-1.diff`（`patch_source=full_file`：模型给完整新文件，`difflib` 生成 unified diff） |
| prompt/context sha | `prompt_sha256` / `context_sha256=c8ca9881aa2dde8abb5f16357743f1d97cdeb9dc446d3acec192131dfa7f8d9b` |
| 应用提交 | `ad24f11f67d869d45f14b663fbe292893bdc9fa0`（`docs/code.diff`） |
| 自检 | 应用后先跑 pytest：`exit_code=0`，再提交（`self_check` 记录） |
| tokens | prompt 2232 / completion 333 / total 2565，11.0s |

复核命令：

```bash
cd minimax
git show ad24f11f6 -- minimax/demo_project/shop/filters.py   # 与 devlog reply 对照
.venv/bin/python -m mmctx.cli verify --context out/s1-order-filter.json --run final4   # ok:true
```

## 3. 总共调了几次

- 生成 `minimax_payload.py`：**3 次**（上表）。
- 业务补丁 / 失败重试：devlog 里 `step=model` 共 **17 条**（含 final4 的 1 条与早期失败重试）。
  统计命令：`grep -h '"step": "model"' devlog/*.jsonl | wc -l`。
- 合计 20 次真实 HTTP 调用；每次的 request_id、usage、latency 都在 devlog/evidence 里。

## 4. 证据路径总表

| 内容 | 路径 |
|---|---|
| 三轮请求体 / 响应 / 落盘代码 / meta | `evidence/01/`、`evidence/02/`、`evidence/03/` |
| 三轮提示词 | `evidence/prompt-01.md`、`prompt-02.md`、`prompt-03.md` |
| 生成脚本（可复跑） | `evidence/harness/generate.py` |
| 生成模块的提交 diff | `docs/minimax-module.diff` |
| 业务补丁的提交 diff | `docs/code.diff` |
| 完整运行链 | `devlog/final4.jsonl`、`devlog/final4.prompt.txt`、`devlog/final4.reply-1.txt`、`devlog/final4.patch-1.diff` |
| 测试日志 | `docs/test-before.log`、`docs/test-after.log`、`docs/test.log` |
| key 安全 | `.env` 被 `.gitignore` 忽略；所有证据文件与日志不含 key |
