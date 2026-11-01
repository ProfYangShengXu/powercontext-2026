# 拾遗 · 学习资料库 + 个性化复习助手

把散落在 Markdown、TXT、PDF 里的学习资料导入本地 **seekdb**，用「向量 + 全文 + 课程过滤」混合检索找回原文；把「不懂 / 已掌握」与复习状态写入 **PowerContext**，重启后仍能生成优先复习建议。

- 资料正文只存 seekdb；学习者状态只存 PowerContext，不重复存资料。
- 全流程不调用 LLM API，不需要任何云端密钥或账号。
- 单机单用户，中文单页 UI，无登录与路由跳转。

## 功能

| 功能 | 行为 | 技术 |
|---|---|---|
| 资料导入 | md / txt / pdf 解析、500 字切块 / 50 字重叠、≤8 条批量写库，来源与课程标签入库 | seekdb |
| 混合检索 | 自然语言提问，向量语义 + `$contains` 全文 + 课程元数据过滤，RRF 融合排序 | seekdb |
| 出处展示 | 每条结果返回原文、来源文件、块号/页码、相关度距离、命中方式，关键词高亮 | seekdb + 原生前端 |
| 复习记忆 | 「标记不懂」写 Source + fact Memory；「已掌握」写 Source；复习建议跨进程召回 | PowerContext |

## 环境与已验证版本

- Linux（本机验证 Manjaro，glibc 2.44；pyseekdb 要求 glibc ≥ 2.28）
- Python 3.12（本机验证 3.12.12）
- [uv](https://github.com/astral-sh/uv) 0.12.10
- pyseekdb 1.4.0.post1（embedded，本地 ONNX 嵌入，384 维）
- FastAPI 0.162.0、uvicorn 0.54.0、pypdf 6.21.4
- PowerContext 1.4.1（server + CLI，默认 SQLite）

## 目录

```text
studydesk/
├── app/
│   ├── main.py        FastAPI 入口、生命周期、单页挂载
│   ├── config.py      端口、切块参数、PowerContext 地址
│   ├── db.py          pyseekdb embedded Client / collection
│   ├── ingest.py      文件发现、PDF/文本解析、切块、批量写入
│   ├── search.py      hybrid_search 封装 + 检索 JSONL 日志
│   ├── memory.py      PowerContext scope / Source / Memory / 复习召回
│   └── web/           原生 HTML/CSS/JS 单页
├── sample_data/       3 个验证资料（2 md + 1 txt）
├── scripts/
│   ├── seed_model.sh  从 ModelScope 预置 all-MiniLM-L6-v2 ONNX 模型
│   └── run_verify.sh  S1-S5 一键验证（含自动重启）
├── site/              展示站静态页（Qoder Sites 发布物料）
├── requirements.txt   锁定版本
├── data/              seekdb 持久化数据（运行时生成，gitignore）
└── logs/              search_*.jsonl / memory_*.jsonl（运行时生成，gitignore）
```

## 首次准备

### 1. 预置嵌入模型

```bash
bash scripts/seed_model.sh
```

下载约 90 MB 的 `all-MiniLM-L6-v2` ONNX 文件到 `~/.cache/pyseekdb/onnx_models/all-MiniLM-L6-v2/onnx/`。脚本从 ModelScope 下载，避开 hf-mirror 跳转到 huggingface.co 导致的失败。

### 2. 安装 Python 依赖

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

### 3. 安装并启动 PowerContext

```bash
uv tool install "powercontext[cli,server]==1.4.1"
powercontext server run
# 默认监听 http://127.0.0.1:8000，SQLite 持久化
```

另开终端验证：

```bash
curl -fsS http://127.0.0.1:8000/health/ready
```

### 4. 启动应用

```bash
.venv/bin/python -m app.main
```

- 应用只监听 `http://127.0.0.1:8001`。
- 启动日志会打印固定 `scope_id`（`idempotency_key=studydesk`），重启后不变。
- 打开 <http://127.0.0.1:8001/>。

PowerContext 未启动时，导入与检索仍可用；`/feedback` 和 `/review` 会返回 503 与明确提示，不会 500。

## 一键验证（推荐）

前置条件：依赖与模型已准备，PowerContext 已启动，8001 端口没有其他服务。脚本会自行启动、重启并停止应用；它不会启动或停止 PowerContext，但会向现有 scope 写入一条验证记忆。

```bash
# 可选：清空本项目运行数据，从零验证
rm -rf data/ logs/

bash scripts/run_verify.sh
```

预期输出（省略中间细节）：

```text
== S1 启动应用 ==
PASS S1 · 服务已启动 http://127.0.0.1:8001
== S2 导入 sample_data ==
PASS S2 · total_chunks=11 files=3
== S3 混合检索「写放大」 ==
PASS S3 · top=数据库笔记.md distance=... result_count=5
== S4 写入薄弱点反馈 ==
PASS S4 · artifact=atomic-memory/art_...@1 source=studydesk-feedback-...
== S5 重启应用并读取复习建议 ==
PASS S5 · status=ready suggestions=... knowledge_point=写放大 ...
日志尾操作: memory.remember,context.prepare

结果: 5 PASS / 0 FAIL
```

任一步失败脚本以非 0 退出，并保留失败响应或应用日志用于排查。S4 的反馈使用真实检索结果与本次唯一标记；S5 按赛题口径验证 `status=ready` 且建议包含知识点「写放大」。

## 手动逐步验证

### S1 启动

```bash
bash scripts/seed_model.sh
uv pip install --python .venv/bin/python -r requirements.txt
powercontext server run     # 终端 A
.venv/bin/python -m app.main  # 终端 B
```

预期：PowerContext 8000 就绪；应用 8001 启动并打印 `scope_id`。

### S2 导入 3 个样例文件

```bash
curl -sS -X POST http://127.0.0.1:8001/ingest \
  -H 'Content-Type: application/json' \
  -d '{"paths":["sample_data"],"course":"db"}'
```

预期：`ok: true`，`files` 共 3 项且 `status: "ok"`，`total_chunks` 为 11。

### S3 混合检索「写放大」

```bash
curl -sS --get http://127.0.0.1:8001/search \
  --data-urlencode 'q=写放大'
```

预期：返回 5 条；第一条 `text` 含「写放大」，`source_file` 为 `数据库笔记.md`，响应含 `distance` 与 `doc_id`。浏览器里会高亮命中词并显示出处、位置、距离。

### S4 标记薄弱点

把上一步首条的 `doc_id` 填入：

```bash
curl -sS -X POST http://127.0.0.1:8001/feedback \
  -H 'Content-Type: application/json' \
  -d '{"doc_id":"<doc_id>","verdict":"weak","note":"写放大总记混"}'
```

预期：HTTP 200，`memory_written: true`，响应含 `artifact` 与 `artifact_ref`（如 `atomic-memory/art_...@1`），`source.status: accepted`。

页面上也可直接点某条结果右上角的「标记不懂」。

### S5 重启并读取复习建议

1. 在终端 B 停止应用（Ctrl+C），再次执行 `.venv/bin/python -m app.main`。
2. 查询：

```bash
curl -sS http://127.0.0.1:8001/review
```

预期：`status: "ready"`，`suggestions` 中包含「写放大」；`raw_content` 为 PowerContext 返回的完整上下文。

```bash
tail -n 2 logs/memory_*.jsonl
```

末两条应是 `memory.remember` 与 `context.prepare`，包含完整请求与响应。

### 页面流程

1. 打开 <http://127.0.0.1:8001/>。
2. 导入区保持 `sample_data` / `db`，点「导入」。
3. 检索「写放大」，查看带出处、位置、相关度距离的结果与高亮。
4. 点结果右上角「标记不懂」，下方「优先复习」出现该薄弱点。
5. 重启应用并刷新页面，复习建议仍在。

## 数据、日志与验证留痕

- `data/seekdb/`：seekdb embedded 持久化目录；重启不丢。
- `logs/search_YYYYMMDD.jsonl`：每次检索的请求、原始 ids/documents/metadatas/distances、归一化结果与耗时。
- `logs/memory_YYYYMMDD.jsonl`：每次 Source、Memory、context/prepare 的完整请求、响应与耗时；scope 启动日志打印在终端。
- 两者均被 `.gitignore` 忽略，运行时生成。
- 验证截图、反馈/复习响应与记忆日志样例在 `../dev-records/M2-webui/`、`../dev-records/M3-memory/`。
- M4 从零验收输出在 `../dev-records/M4-verify/verify.log`；同目录的 `search_sample.jsonl`、`memory_sample.jsonl` 是这次运行生成的检索与记忆日志样例。
- M5 展示站截图与本地预览检查在 `../dev-records/M5-site/`。

## 展示站（M5）

`site/` 是提交用的静态单页展示站：项目一句话、界面实拍、seekdb / PowerContext 分工、GitHub 与复现链接，以及一句使用效果说明。

版式采用「奶油手账」：新鲜牛奶奶油底、抹茶绿主色、一个团子吉祥物，命中词用奶油黄荧光笔；首屏是三行事实主张加一张真实的检索结果卡，界面区只保留一张实拍，可点开原图。全站无衬线、文案精简。

```bash
cd site
python3 -m http.server 8010 --bind 127.0.0.1
# 打开 http://127.0.0.1:8010/
```

展示站不依赖应用进程，也不包含运行时数据。正式发布通过 Qoder Sites 完成，地址发布后补入 `../docs/SUBMISSION.md`。

## 配置

无需任何外部 API 密钥。可选环境变量：

```bash
# PowerContext 地址，默认值如下；仅在改端口/主机时设置
export POWERCONTEXT_BASE_URL=http://127.0.0.1:8000
```

端口约定：PowerContext 8000，拾遗应用 8001。README 中所有示例均为本地占位地址，不包含真实凭据。

## 已知限制

- 单机单用户，无认证；不要把 8001 直接暴露到公网。
- 不做 LLM 答案生成，返回检索到的原文段落，避免评审环境依赖模型 API。
- PDF 仅提取文本层，扫描件需要 OCR，当前不处理。
- 中文检索以本地 MiniLM 语义 + 全文关键词兜底；示例数据已验证，换语料需自行评估效果。
- 复习建议受 PowerContext `context/prepare` 的 `max_bytes=4000` 限制；记忆很多时只返回最相关的一批条目，页面内可滚动。
- 重新导入同一课程/文件采用「先删旧块、再批量写入」，不是跨文件事务；导入失败时该文件可能暂时缺块，重试即可。

## 依赖与来源

- 应用代码（`app/`、`static/`、`scripts/`）为本项目原创，未复制或改写第三方项目源码。
- 第三方依赖按 `requirements.txt` 从 PyPI 安装：FastAPI、uvicorn、pypdf、pyseekdb，版本见上；嵌入模型 all-MiniLM-L6-v2 由 pyseekdb 从 ModelScope 下载，未入库。
- PowerContext 以本地服务方式接入（CLI 安装），不修改其源码。
- `sample_data/` 三个样例文件为本项目自写，用于复现检索与复习闭环，不含第三方版权内容。
