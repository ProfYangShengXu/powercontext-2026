# 数据库证据（seekdb 嵌入式实例）

commit: `ad24f11f67d869d45f14b663fbe292893bdc9fa0`
seekdb 数据目录: `/mnt/wsl/mmctx-seekdb`  表: `mm_doc_chunks`  全文 parser: `IK`

## 行数与索引

- 总块数(含历史版本): 49；当前版本: 46；文件数: 10
| doc_kind | 当前块数 | 文件数 |
|---|---|---|
| code | 8 | 4 |
| spec | 21 | 4 |
| test | 17 | 2 |

索引：`ix_mm_fts` FULLTEXT、`ix_mm_vec` / `ix_mm_vec_index_*` VECTOR，另有 `ix_mm_doc(doc_id,is_current)` 与 `ix_mm_kind(doc_kind,is_current)`。

历史版本行是「规格换代」留下的审计轨迹（`is_current=0`），不会进入检索。

## 单条混合召回

`StorePort.search()` 实际执行 **1 条 SQL**，返回 80 行（fts 臂 + vector 臂 UNION ALL），去重后 44 块。

示例命中（前 3）：

- `84edb185a89dfeb18316174762289dbd` corpus/specs/S-02-filtering.md:6-7 arm=fts fts=15.38511281426205 vec=None
- `4c0c2f55e99001bb04a053a2ea2ad311` corpus/specs/S-02-filtering.md:9-10 arm=fts fts=12.7093645260643 vec=None
- `4960b4c7ba1978ae6ed2426f469b4790` demo_project/shop/filters.py:11-39 arm=fts fts=12.038939220167217 vec=None

## 上下文包里的 chunk_uid 可以回查数据库

`out/s1-order-filter.json` 的 12 条来源中，**12 条**能用 `chunk_uid` 在 `mm_doc_chunks` 回查到（is_current=1）。

## 候选 SQL（与 `mmctx/adapters/seekdb_store.py:_CANDIDATE_SQL` 一致）

```sql
SELECT ... FROM (
  SELECT ..., MATCH(searchable) AGAINST (%(q)s IN NATURAL LANGUAGE MODE) AS fts_score, NULL AS vec_dist, 'fts' AS arm
  FROM mm_doc_chunks WHERE is_current = 1 AND MATCH(searchable) AGAINST (%(q)s IN NATURAL LANGUAGE MODE)
  ORDER BY fts_score DESC LIMIT %(k)s
) fts_arm
UNION ALL
SELECT ... FROM (
  SELECT ..., NULL AS fts_score, l2_distance(embedding, %(v)s) AS vec_dist, 'vector' AS arm
  FROM mm_doc_chunks WHERE is_current = 1
  ORDER BY l2_distance(embedding, %(v)s) APPROXIMATE LIMIT %(k)s
) vec_arm
```

## EXPLAIN（关键行，完整文本见 `docs/explain.txt`）

```text
===============================================================================================
|ID|OPERATOR                                   |NAME                    |EST.ROWS|EST.TIME(us)|
-----------------------------------------------------------------------------------------------
|0 |UNION ALL                                  |                        |11      |4225        |
|1 |├─SUBPLAN SCAN                             |fts_arm                 |10      |4213        |
|2 |│ └─TOP-N SORT                             |                        |10      |4213        |
|3 |│   └─TEXT RETRIEVAL SCAN                  |mm_doc_chunks(ix_mm_fts)|12      |4206        |
|4 |└─SUBPLAN SCAN                             |vec_arm                 |1       |12          |
```
