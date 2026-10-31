# 题四 财报 PDF 证据页检索 · 召回率工程报告

> 目标：`evidence-page Recall@20 ≥ 0.70`（题四 50 分门槛）
> 分支：`feat/pdf-recall`（从 `feat/all-challenges` 起）
> 评测工具：`tools/pdf_recall_eval/`（正式交付，见该目录 README）

## 0. 结论

| 集合 | 规模 | Recall@20（前 → 后） |
|---|---|---|
| 公开 PepsiCo 5 题（真实 FinanceBench 标注） | 5 题 / 503 页 | **0.700 → 0.900** |
| 5 份 10-K 公开题（PepsiCo + AMD + AMEX + Boeing + 3M） | 29 题 / 1326 页 | **0.397 → 0.879** |
| 代理集（确定性模板） | 100 题 / 503 页 | 0.920 → 0.900（R@5 0.75→0.69，MRR 0.468→0.507） |

5 份文档的**单文档** Recall@20 全部 ≥ 0.70（最差 0.857，PepsiCo/AMD/AMEX/3M 0.857–0.900，Boeing 0.905）。

主因不是向量（offline 判题环境没有 embedding 模型，`capabilities.hybrid=False`），而是两条纯本地信号：

1. **章节/报表类型召回臂**：财报问题的金标页大量是四张报表本身（利润表/资产负债表/现金流量表），问题却只给主题词（"operating margin"、"effective tax rate"、"quick ratio"）。按金标页自己的标题识别报表类型，作为独立召回臂融合。
2. **词形折叠（Porter stem）**：`geographies ↔ geographic`、`liabilities ↔ liability`、`operating ↔ operations` 这类同词根不匹配，是 A 类漏召回的主因；同时保留原始词形，避免 `operates` 被压成高频 `oper` 后 IDF 失效。

## 1. 评测方法（批 0）

### 1.1 代理集（100 条，必须交付）

`tools/pdf_recall_eval/build_dataset.py` 用固定 seed（20221031）从公开 PepsiCo 2022 10-K 生成，无模型、无网络、可复现：

| 造法 | 条数 | 例 |
|---|---|---|
| 页内数字锚定 | 27 | `What was Net revenue reported as in PepsiCo's fiscal 2022 consolidated statements?` → p40 |
| 实体锚定（章节标题） | 17 | `... what is covered under "Consolidated Statement of Cash Flows"?` → p64 |
| 页首句反推 | 56 | `... what does the filing state about sources uses cash ...?` → p52 |

落库：`tools/pdf_recall_eval/proxy_dataset.jsonl`。

**局限（如实说明）**：模板题大量复用页内原词，比真实 FinanceBench 问题容易；它的 Recall@20 很高（0.92 起），不能单独作为判据。真实判据用下面 1.2。最终方案在代理集上 R@20 0.920 → 0.900，原因是模板里的 "Net revenue" 类数字题会触发利润表章节臂，而金标页常常是另一张附表；这是章节臂的代价，在真实题上被 +0.43 的收益抵消。

### 1.2 真实标注集

- `tools/pdf_recall_eval/real_questions.jsonl`：公开工作量里的 5 条 FinanceBench 原题；页码由上游 `evidence_page_num`（0-based）+1 得到。
- 额外 24 条：AMD / American Express / Boeing / 3M 的公开题，同样取上游页码。这 4 份 PDF 只在开发时下载到仓库外（`/mnt/d/pc-work/.pdf_recall_eval/pdfs`），**不入库**。它们不是隐藏集的 5 份文档（隐藏集具体是哪 5 份未知），只作为跨文档泛化验证。

### 1.3 explain 探针

`evaluate.py --explain` 对每条题输出：离线 BM25 臂排名、公开 search 臂排名、融合排名、每条 distilled query 的排名、缺失的臂，并判 A/B/C：

- A：没进任何候选池（召回层：分词/词形/词表）
- B：进池但融合排名超出 20（融合/排序）
- C：进了前 20，但错页排在它前面（精度/精排）

## 2. ① 批 0~4 每批读数

29 题 / 5 文档统一口径；括号内为单文档最差值。

| 批次 | 改动 | Recall@20 | Recall@5 | MRR |
|---|---|---|---|---|
| 批 0 | 原始实现（baseline） | 0.3966 | 0.3103 | 0.1783 |
| 批 1 | capabilities 探测 + hybrid 尝试（offline 自动降级 fts） | 0.3966 | 0.3103 | 0.1783 |
| 批 2 | 可插拔精排端口 + Noop 回退 + 词法精排（在最终候选池上测） | 0.8793 | 0.4827 | 0.3622 → 0.3657 |
| 批 3 | 词形折叠 + 覆盖率 + 页尾窗口修复 + 缓存键 | 0.4483（最差 0.143） | 0.3448 | 0.2534 |
| 批 4 | 章节/报表类型召回臂 | **0.8793**（最差 0.857） | **0.4827** | **0.3657** |

> 批 2 的精排依赖最终候选池，所以 MRR 前后对比放在批 3/4 之后测；批 2 的独立增量是 MRR 0.3622 → 0.3657，Recall@20 逐位不变。
> 批 3 的 0.4483 是"关掉章节臂、保留词形+覆盖率+尾窗"的读数；章节臂是批 4 的增量（0.4483 → 0.8793）。

### 单文档明细（最终）

| 文档 | 题数 | Recall@20 | Recall@5 | MRR |
|---|---|---|---|---|
| PEPSICO_2022_10K | 5 | 0.9000 | 0.2000 | 0.3284 |
| AMD_2022_10K | 7 | 0.8571 | 0.8571 | 0.5238 |
| AMERICANEXPRESS_2022_10K | 7 | 0.8571 | 0.2857 | 0.2350 |
| BOEING_2022_10K | 7 | 0.9048 | 0.7143 | 0.4873 |
| 3M_2022_10K | 3 | 0.8889 | 0.0000 | 0.0803 |

公开 PepsiCo 5 题（题四真正公开的那 5 条）：Recall@20 **0.70 → 0.90**；Recall@5 0.60 → 0.20；MRR 0.3733 → 0.3284。R@20 达标，R@5/MRR 变差是因为章节臂把"正确的报表页"从后面提到前面，但仍排在若干同章节页之后；门槛只看 R@20。

## 3. ② 消融表（最终代码上逐项关闭）

命令：`/mnt/d/pc-work/.pdf_recall_eval/run_multi.sh`（29 题 / 5 文档）。

| 变体 | Recall@20 | Recall@5 | MRR | 结论 |
|---|---|---|---|---|
| 完整 | 0.8793 | 0.4827 | 0.3657 | — |
| 关掉向量路（强制 fts） | 0.8793 | 0.4827 | 0.3657 | offline 后端本来就没有向量；实现和降级有测试 |
| 关掉精排（Noop） | 0.8793 | 0.4827 | 0.3622 | R@20 不变；MRR −0.0035 |
| 关掉覆盖率 | 0.8793 | 0.4827 | 0.3664 | 加法覆盖率中性；乘法覆盖率见 §5 |
| **关掉章节臂** | **0.4483** | 0.3448 | 0.2534 | 降 0.431，最大单项 |
| **关掉词形折叠** | **0.6150** | 0.3736 | 0.2926 | 降 0.264 |
| 关掉页尾窗口修复 | 0.8793 | 0.4827 | 0.3817 | 本集合 R@20 无变化（正确性修复，保留） |

## 4. ③ 全量测试

见文末"验证记录"：`make -C competition test` 的逐题结果，以及产品新增单测 `tests/test_pdf_pages.py`（8 passed）。

## 5. ④ 哪些招没用、为什么

1. **向量臂在 offline 判题里不可用。** `_preferred_search_mode` 先探 `capabilities.hybrid`，为真走 `mode="hybrid"`，否则 fts；hybrid 被拒时再退 fts，探针失败也退 fts，均有单测。但判题环境"不调用模型或在线服务"，SQLite 后端 `capabilities.hybrid=False`，所以 Recall@20 前后完全一致（0.3966 → 0.3966）。这招是**为 online/向量后端准备的正确性改动，不是本题的召回来源**。
2. **乘法覆盖率（方案原建议 `score = base * coverage`）反而掉分。** 9 题集上 R@20 0.8793 → 0.8448。原因：报表页与问题只共享少量泛词，乘法把它压成 0，而它恰是金标页。改成**加法**（`bm25 + coverage`，coverage∈[0,1]）后与不加持平（0.8793），保留作 tie-break / 无 passage 命中但词项有覆盖的兜底。
3. **top-50 提升式精排掉 Recall@20。** 用 phrase / numeric / coverage 把 21–50 名提进前 20 的三种变体，AMEX 从 0.857 掉到 0.429–0.571，3M 从 0.889 掉到 0.556。词的精确匹配在真实问题上不如"报表类型"可靠。最终落地为**只在已返回的 20 条内部重排**（membership 不变，R@20 有保证），MRR 0.3622 → 0.3657。
4. **父子块没做。** 判题 pipeline 是"每页一个 entry"，entry 级排序本来就是页级聚合；再造父子块没有可测增量，按批 4"没有增量就回退"处理。
5. **页尾窗口修复没有可测召回增量。** 审计发现 117/503 页（23%）的 90 token 之后内容拿不到 BM25 分；本 29 题集合里金标页恰好没落在尾部，所以 R@20 不变。作为正确性修复保留（单测覆盖）。
6. **代理集不能当主判据。** 100 条模板题里有大量页内原词，baseline 就有 0.92；真实题 baseline 只有 0.397。所有取舍以 29 条真实题为准。

## 6. ⑤ 回退

- 乘法覆盖率 → 回退为加法（保留覆盖率信号，去掉有害的乘性惩罚）。
- top-50 提升式精排 → 回退为返回集合内重排（端口仍在，等可信的语义 cross-encoder 再放开）。
- 父子块 / 章节字段之外的结构化切分 → 不做（无可测增量）。

## 7. 风险与局限

- 章节意图是一张 10-K 领域关键词表（利润表/资产负债表/现金流量表/分部/法务/风险/地域/业务），对 10-K 之外的财报（earnings release、10-Q、非英文）泛化未验证。
- 29 题样本小，0.879 与 0.845 这类差异可能是噪声；但"章节臂 +0.43"和"词形折叠 +0.26"远大于噪声量级。
- 判题是"任一组 <0.70 即 0 分"，所以报告按单文档给最差值（0.857），不是只看宏平均。
- 未验证隐藏集确实是这 5 份文档；只用这 5 份做机制泛化验证。

## 8. 验证记录

产品新增单测：

```
PYTHONPATH=src competition/.venv/bin/python -m pytest tests/test_pdf_pages.py -q
8 passed
```

公开自测（在 `competition/` 的锁定 venv 下逐题运行）：

| 目标 | 结果 |
|---|---|
| test-csv | 17 passed |
| test-pi | 15 passed |
| test-pdf | 6 passed（改后重跑两次：31s / 36s） |
| test-rerank | 10 passed |
| test-incremental | 14 passed |
| test-count-trigger | 见下 |
| test-embedding | 8 passed |
| test-lifecycle | 4 passed |
| test-evidence | 3 passed |
| test-hybrid | 1 passed |

`test-count-trigger` 在本机（7GB WSL，仓库在 `/mnt/d`）有 3 个自动触发用例超时失败
（`Native automatic processing timed out` / `No native generation was attempted`）。
同一组测试在**改动前的基线 commit `097f98641`** 的独立工作树上跑出 **10 failed / 7 passed**
（本分支 3 failed / 14 passed）。同一机器、同一测试文件，基线更差，说明是本机时序/性能
问题，不是本次改动引入的回归；其余 9 题全绿。
