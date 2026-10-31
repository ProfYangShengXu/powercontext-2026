# 本机环境验证记录（2026-10-31，Manjaro Linux）

> 用途：README「运行说明」的事实来源；评审复现环境的参照。以下全部实测通过。

## 已验证的组件版本

| 组件 | 版本 | 验证方式 |
|---|---|---|
| Python | 3.12.12（venv；3.11+ 均可，pyseekdb 建议 ≤3.13，3.14 走 sentence-transformers 后端） | `uv venv --python 3.12` |
| pyseekdb | 1.4.0.post1（embedded 模式） | 混合检索冒烟测试 |
| PowerContext | 1.4.1（`uv tool install "powercontext[cli,server]==1.4.1"`） | 记忆写入 + 跨重启召回 |
| uv | 0.12.10 | 安装工具 |
| glibc | 2.44（seekdb 要求 ≥2.28） | `ldd --version` |

## seekdb 验证结论（dev-env/seekdb_smoke.py）

- embedded `pyseekdb.Client()` 直接可用，自动嵌入 384 维（本地 ONNX，无 API key）
- 中文资料：`hybrid_search`（knn「事务并发控制的风险」+ `$contains`「死锁」+ RRF）第一名正确命中 2PL/死锁文档
- 纯语义 `query`：「为什么写多读少适合 LSM 结构」→ LSM 文档 top1（dist 0.427）
- 元数据过滤 `where={"course": ...}` 正常
- 性能：模型缓存后 5 条 add 0.6s、检索 0.1s

### 两个已踩过的坑（已写进 PRD §4.2，README 必须覆盖）

1. **模型下载**：默认下载链 hf-mirror.com → 308 → huggingface.co（直连失败）。
   解决：`qoder/scripts/seed_model.sh` 从 ModelScope 下载 6 个文件到
   `~/.cache/pyseekdb/onnx_models/all-MiniLM-L6-v2/onnx/`。
2. **批量写入超时**：首次冷启动批量 add 可能超 10s 语句超时（ob_query_timeout，
   错误码 4012）。解决：≤8 条/批；如仍超时可在连接后执行
   `SET GLOBAL ob_query_timeout=60000000`。

## PowerContext 验证结论（dev-env/pc_smoke.py）

- `powercontext server run` → 127.0.0.1:8000，默认 SQLite 存储
- 未配置推理模型时：显式 `memory/remember` 可用（不需要模型）；`context/prepare`
  走全文召回，query 含「写放大 复习」能命中 Memory 文本中的「写放大」
- 写入 → **杀掉 server 重启** → 同 scope 再次 prepare：`status: ready`、内容命中
  ——跨重启复用成立，对应赛题「状态能复用」核对点
- scope 建议固定 `idempotency_key`，重启后重建拿回同一 scope

## 端口约定

| 服务 | 端口 |
|---|---|
| PowerContext server | 8000（默认，别改，README 按 8000 写） |
| 拾遗应用 | 8001（避开 8000） |

## 本机当前状态（开发时就绪）

- PowerContext server 正在运行（http://127.0.0.1:8000）
- seekdb venv：`~/Desktop/learning/oceanbase-comp/dev-env/seekdb-venv`（仅验证用；
  项目自己建 venv，别依赖这个）
- 模型已种子：`~/.cache/pyseekdb/onnx_models/all-MiniLM-L6-v2/onnx/`（6 文件齐全）
