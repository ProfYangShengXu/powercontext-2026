# 拾遗 · studydesk

本地学习资料库 + 复习助手。当前为 **M3：seekdb 资料导入与混合检索 + PowerContext 复习记忆闭环**；M4 的完整验证脚本与评审 README 待补。

## 环境

- Python 3.12
- [uv](https://github.com/astral-sh/uv)
- PowerContext 1.4.1（本机已验证：`uv tool install "powercontext[cli,server]==1.4.1"`）
- 本机已验证：pyseekdb 1.4.0.post1、FastAPI 0.162.0、pypdf 6.21.4

## 首次准备

```bash
# 1. 预置本地嵌入模型（约 90 MB，从 ModelScope 下载）
bash scripts/seed_model.sh

# 2. 创建虚拟环境并安装依赖
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt

# 3. 启动 PowerContext（另开终端；默认 SQLite，监听 127.0.0.1:8000）
powercontext server run
```

## 启动

```bash
.venv/bin/python -m app.main
# 服务只监听 http://127.0.0.1:8001
# 启动日志会打印固定 scope_id（idempotency_key=studydesk）
```

PowerContext 默认地址为 `http://127.0.0.1:8000`，可用环境变量 `POWERCONTEXT_BASE_URL` 覆盖。PowerContext 未启动时应用仍可检索，`/feedback` 与 `/review` 会返回 503 和明确提示，而不是 500。

启动后打开 <http://127.0.0.1:8001/>。导入区默认填好 `sample_data` 与课程 `db`，点「导入」即可；在检索区输入「写放大」，能看到带出处、位置与相关度距离的段落列表，命中词会高亮。对结果点「标记不懂」后，右侧「优先复习」会从 PowerContext 召回这条薄弱点。

## 页面（M3）

- **资料导入**：填项目内的目录/文件路径（多个用逗号分隔）与课程标签，返回每个文件的切块数与写入结果。重复导入会覆盖同课程、同文件的旧片段。
- **混合检索**：输入问题，可选课程过滤；向量语义 + `$contains` 全文 + RRF 融合，结果展示原文、来源文件名、位置、匹配方式与距离，命中关键词高亮；每条结果可标记「不懂」或「已掌握」。
- **优先复习**：调用 `context/prepare` 读取跨会话记忆，展示薄弱点建议；重启应用或 PowerContext 后仍可召回。
- 中文单页、原生 HTML/CSS/JS，无登录、多用户与页面路由；1280×720 下无横向滚动。

## 验收示例

```bash
# 导入 sample_data 下的 3 个资料文件
curl -sS -X POST http://127.0.0.1:8001/ingest \
  -H 'Content-Type: application/json' \
  -d '{"paths":["sample_data"],"course":"db"}'

# 混合检索：向量 + $contains 全文 + RRF
curl -sS --get http://127.0.0.1:8001/search \
  --data-urlencode 'q=写放大'

curl -sS --get http://127.0.0.1:8001/search \
  --data-urlencode 'q=死锁是怎么办' \
  --data-urlencode 'course=db'

# 标记薄弱点：响应包含 artifact 地址
curl -sS -X POST http://127.0.0.1:8001/feedback \
  -H 'Content-Type: application/json' \
  -d '{"doc_id":"<检索结果中的 doc_id>","verdict":"weak","note":"写放大总记混"}'

# 读取复习建议
curl -sS http://127.0.0.1:8001/review

# 重启 app 进程后再次执行上面的 /review，建议仍在
# PowerContext 请求/响应逐条记录
tail -n 2 logs/memory_*.jsonl
```

导入后应有 3 个文件的 `chunks > 0`；「写放大」首条来自 `数据库笔记.md`，「死锁是怎么办」返回 `事务笔记.md` 中的死锁段落。反馈后 `/review` 的 `suggestions` 应包含「写放大」；即使 `doc_id` 不在 seekdb 中，也会按备注记录薄弱点。

## 目录

```text
app/
  main.py       FastAPI 入口、生命周期与静态页挂载
  db.py         pyseekdb 嵌入式 Client / collection
  ingest.py     文件解析、切块、≤8 条批量写入
  search.py     hybrid_search 与 JSONL 检索日志
  memory.py     PowerContext scope / Source / Memory / 复习召回与记忆日志
  web/          单页 UI（index.html / styles.css / app.js）
sample_data/    验证用资料
scripts/        模型种子脚本
data/           seekdb 持久化数据（运行时生成）
logs/           检索日志与记忆日志（运行时生成）
```

## 当前边界（M3）

- 不调用 LLM，不做答案生成。
- 不做登录、多用户和页面路由。
- 资料正文只存 seekdb；学习者状态只存 PowerContext，两边不重复存资料。
- PowerContext 不可用时检索仍可用，记忆接口返回明确 503。
- PDF 仅提取文本层，不包含 OCR。
