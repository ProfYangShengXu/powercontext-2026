# MiniMax 参与本仓库代码的存证

这里的文件证明：`minimax/mmctx/adapters/minimax_payload.py` 的**实现代码**由
`MiniMax-Text-01` 生成，并由脚本直接写入仓库文件（不是人工抄写）。

- 模型：`MiniMax-Text-01`
- 端点：`https://api.minimax.chat/v1` + `/text/chatcompletion_v2`
- 调用日期：2026-10-31（UTC）
- 对应提交：`4fc7a68ce3addc7e7c7f08198e51e75bd920d8db`（`git show 4fc7a68ce -- minimax/mmctx/adapters/minimax_payload.py`）
- 调用次数：3 轮（生成 → 按失败测试修正 → 再修正）
- 生成脚本：`evidence/harness/generate.py`（读 `.env` 的 key，只放进
  `Authorization` 头；`request.json` 里已脱敏）
- 最终落盘：`mmctx/adapters/minimax_payload.py`（sha256
  `d4c5925a847455acc8ecd2a5788148b269a6b9392fd3525633290c8a829cbb5f`，
  与 `03/generated.py` **逐字节相同**）

## 三轮记录

| 轮次 | 请求 | 响应原文 | 落盘代码 | 验收结果 | tokens |
|---|---|---|---|---|---|
| 01 | `01/request.json` | `01/response.json`（`01/reply.txt`） | `01/generated.py` | 10 个契约测试 2 失败（model 回退、非 200 分类） | 2581 |
| 02 | `02/request.json` | `02/response.json`（`02/reply.txt`） | `02/generated.py` | 9 过 1 失败（HTTP 200 + 非 dict body 漏判） | 2787 |
| 03 | `03/request.json` | `03/response.json`（`03/reply.txt`） | `03/generated.py` | 10/10 通过 | 2739 |

每轮的提示词原文：`prompt-01.md` / `prompt-02.md` / `prompt-03.md`
（第 2、3 轮包含上一轮失败断言与上一版代码）。

## 怎么复核

```bash
cd minimax
sha256sum mmctx/adapters/minimax_payload.py evidence/03/generated.py   # 应相同
git log --oneline -- mmctx/adapters/minimax_payload.py                 # 对应提交
git show <commit> -- mmctx/adapters/minimax_payload.py                 # 该文件的逐行 diff
.venv/bin/python -m pytest -q tests/test_minimax_payload.py            # 10 passed
```

提交 SHA 与行号对应关系写在 `docs/MiniMax参与记录.md`。
