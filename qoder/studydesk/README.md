# 拾遗 · studydesk

本地学习资料库 + 复习助手。当前为 **M2：seekdb 导入、混合检索与单页 Web UI**；PowerContext 记忆闭环（M3）尚未接入。

## 环境

- Python 3.12
- [uv](https://github.com/astral-sh/uv)
- 本机已验证：pyseekdb 1.4.0.post1、FastAPI 0.162.0、pypdf 6.21.4

## 首次准备

```bash
# 1. 预置本地嵌入模型（约 90 MB，从 ModelScope 下载）
bash scripts/seed_model.sh

# 2. 创建虚拟环境并安装依赖
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

## 启动

```bash
.venv/bin/python -m app.main
# 服务只监听 http://127.0.0.1:8001
```

启动后打开 <http://127.0.0.1:8001/>。导入区默认填好 `sample_data` 与课程 `db`，点「导入」即可；在检索区输入「写放大」，能看到带出处、位置与相关度距离的段落列表，命中词会高亮。

## 页面（M2）

- **资料导入**：填项目内的目录/文件路径（多个用逗号分隔）与课程标签，返回每个文件的切块数与写入结果。重复导入会覆盖同课程、同文件的旧片段。
- **混合检索**：输入问题，可选课程过滤；向量语义 + `$contains` 全文 + RRF 融合，结果展示原文、来源文件名、位置、匹配方式与距离，命中关键词高亮。
- **优先复习**：占位卡片，M3 接入 PowerContext 后显示跨会话的薄弱点建议。
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

# 检索日志：每次请求一行 JSON
tail -n 2 logs/search_*.jsonl
```

导入后应有 3 个文件的 `chunks > 0`；「写放大」首条来自 `数据库笔记.md`，「死锁是怎么办」返回 `事务笔记.md` 中的死锁段落。

## 目录

```text
app/
  main.py       FastAPI 入口、生命周期与静态页挂载
  db.py         pyseekdb 嵌入式 Client / collection
  ingest.py     文件解析、切块、≤8 条批量写入
  search.py     hybrid_search 与 JSONL 检索日志
  web/          单页 UI（index.html / styles.css / app.js）
sample_data/    验证用资料
scripts/        模型种子脚本
data/           seekdb 持久化数据（运行时生成）
logs/           检索日志（运行时生成）
```

## 当前边界（M2）

- 不调用 LLM，不做答案生成。
- 不做登录、多用户和页面路由。
- 复习建议仅是占位，PowerContext 记忆闭环在 M3 接入。
- PDF 仅提取文本层，不包含 OCR。
