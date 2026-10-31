# Qoder 开发提示词包 — 拾遗

用法：在 Qoder 中打开仓库根目录，**按顺序**把每个阶段的提示词发给 Qoder。每个阶段结束做两件事再进下一阶段：

1. **验收**：跑该阶段的「验收命令」，输出符合预期才继续；
2. **留痕**：导出本阶段关键对话截图（或整段录屏），存到 `qoder/dev-records/M<n>-<主题>/`。这是赛题「Qoder 开发可核对」的评分材料。

提示词刻意保留了需求描述的颗粒度（做什么 + 边界 + 验收），把「怎么实现」留给 Qoder 拆解——这正是它要展示的环节，别把它变成抄代码的复读机。

阶段挂起超过 2 轮修不好时：缩范围（砍非核心字段/样式），保住闭环，别恋战。

---

## M0 · 开场上下文（新会话第一发）

```text
我们要一起做一个参赛项目「拾遗」：本地学习资料库 + 复习助手。
先读 qoder/docs/PRD.md，这是完整的产品需求和架构约束。

你的任务：通读后给我一个 M1 阶段的实现拆解（文件结构、模块职责、实现顺序），
不要写代码。拆解里必须体现：pyseekdb 嵌入式用法、批量写入限流、
检索日志落盘这三点，PRD §4.2 和 §5 的接口表是你的硬约束。
```

**留痕**：这张「需求拆解」对话截图最重要——评分项里 Qoder 参与需求拆解的直接证据。

---

## M1 · seekdb 导入与检索（后端核心）

```text
按你刚才的拆解，实现 M1：项目骨架 + seekdb 导入/检索模块。

要求：
1. Python 3.12 + FastAPI；目录结构按 PRD §5（app/ingest.py, app/search.py, app/main.py）
2. seekdb 用 pyseekdb 嵌入式模式（Client()），数据目录 ./data/seekdb 持久化
3. POST /ingest：收 {paths: [...], course}，支持 .md/.txt/.pdf（PDF 解析用 pypdf），
   切块 ≤500 字符、重叠 50；每批 ≤8 条调 collection.add（PRD §4.2 超时坑）；
   返回每个文件的 chunk 数
4. GET /search?q=&course=：必须用 collection.hybrid_search（knn + $contains + RRF），
   course 参数作为元数据过滤；返回 [{text, source_file, distance, matched_by}]
5. 每次检索把「请求参数 + 返回 ids/distances」追加写 logs/search_YYYYMMDD.jsonl —— 评审要靠它核对 seekdb 真实在工作
6. sample_data/ 里放 3 个示例文件：数据库笔记.md（含 B+树/LSM/写放大段落）、
   事务笔记.md（含 MVCC/2PL/死锁）、paper-notes.txt（英文段落），每个 ≥8 段

验收命令（全过才算完）：
  curl -X POST :8001/ingest -d '{"paths":["sample_data"],"course":"db"}'
  curl ':8001/search?q=写放大'   → 第一条 text 含「写放大」且 source_file 正确
  curl ':8001/search?q=死锁是怎么办' → 返回事务笔记相关段落
  cat logs/search_*.jsonl | tail -2   → 有完整请求/响应记录
```

**留痕**：编码过程截图 + 验收命令输出的终端截图。

---

## M2 · Web UI

```text
实现 M2：单页 Web UI，让 M1 的功能可用、可演示。

要求：
1. GET / 返回单页（原生 HTML/JS 或 Jinja2，不引前端框架）
2. 三个区块：导入（选目录/文件 + 课程标签 + 按钮）、提问（输入框 + 结果列表）、
   复习建议（占位卡片，M3 接数据）
3. 检索结果每条显示：段落文本、出处文件名、相关度；命中关键词高亮
4. 中文界面，样式简洁即可，但要在 1280×720 无横向滚动
5. 不做：登录、多用户、路由跳转

验收：浏览器打开 :8001，导入 → 提问「写放大」→ 能看到带出处的段落列表。
```

**留痕**：UI 成品截图（这张后面要放到 Sites 展示站）。

---

## M3 · PowerContext 记忆闭环

```text
实现 M3：接入 PowerContext（127.0.0.1:8000），做复习记忆闭环。

先读 PRD §3 分工和 §5 的 PowerContext 接口表，然后实现 app/memory.py：
1. POST /feedback：{doc_id, verdict: weak|mastered, note} →
   先 POST /v1/sources/content 采集一条 Source（含提问上下文），
   verdict=weak 时再 POST /v1/memory/remember 写一条 fact；
   Memory 的 text 必须包含知识点实体词（如「LSM-Tree 写放大」），
   不能只写抽象描述 —— 未配推理模型时召回是全文匹配（PRD §4.3）
2. GET /review：POST /v1/context/prepare {query:"薄弱点 复习", max_bytes:4000} →
   解析出建议列表给前端复习卡片；status=empty 时显示「暂无记忆，先去学习吧」
3. 所有 PowerContext 请求/响应追加写 logs/memory_YYYYMMDD.jsonl
4. scope 用固定 idempotency_key="studydesk"，启动时创建并打印 scope_id
5. PowerContext 未启动时，/review 和 /feedback 返回明确错误提示而不是 500

验收（这组是赛题「状态能复用」的核对点，逐条执行并记录输出）：
  curl -X POST :8001/feedback -d '{"doc_id":"x","verdict":"weak","note":"写放大总记混"}'
  → 响应含 artifact 地址
  # 重启 app 进程：
  curl :8001/review → suggestions 里包含「写放大」，status=ready
  tail -2 logs/memory_*.jsonl → 有 remember 和 prepare 的完整记录
```

**留痕**：这一阶段务必录屏（feedback → 重启 → review 全过程）——最硬的验收证据。

---

## M4 · 验证样例脚本 + README

```text
实现 M4：一键验证脚本和评审入口 README。

1. scripts/run_verify.sh：按 PRD §6 的 S1-S5 顺序执行（S5 包含自动重启进程再查询），
   每步打印 PASS/FAIL 和关键输出；任何一步 FAIL 退出码非 0
2. requirements.txt：锁定本机验证过的版本（pyseekdb==1.4.0.post1 等）
3. README.md（qoder/<项目>/README.md，评审第一个看到的）按 PRD §6 写：
   环境要求 / 模型种子步骤 / PowerContext 安装启动 / 应用启动 / 验证样例逐步操作 / 已知限制
4. README 里所有配置示例一律占位值（如 your-api-key-here），不许出现任何真实凭据
5. .gitignore：data/ logs/ __pycache__/ .env

验收：清理 data/ logs/ 后从零跑 bash scripts/run_verify.sh，5 步全 PASS。
```

**留痕**：全绿终端截图。

---

## M5 · Qoder Sites 展示站

```text
实现 M5：项目展示站，用 Qoder Sites 发布。

单页静态站内容（放 qoder/<项目>/site/）：
1. 项目一句话 + 解决什么问题（PRD §1 痛点）
2. 核心功能三卡片（F1/F2/F3）+ 一张 M2 的 UI 截图
3. 技术说明小节：seekdb 承担检索 / PowerContext 承担记忆（PRD §3 那张图的文字版）
4. 链接：GitHub 仓库地址、本地复现指引（指向 README）
5. 预留「使用效果」区块（数据后面补）

发布流程（手动，做完站后我来操作）：
Qoder 里 Sites → 创建 → 部署 → 可见范围选「任何人都可访问」→
无痕窗口验证可打开 → 把网址记到 SUBMISSION.md
```

**留痕**：Sites 发布设置页截图（要能看到可见范围选项）。

---

## 日常卡住时的追问模板

```text
<报错贴这里>。先解释根因，再给最小修改。
不要重构无关代码，不要升级依赖版本。
```

```text
这个方案和 PRD §5 的接口表冲突了。以接口表为准重新设计，说明你改了什么。
```
