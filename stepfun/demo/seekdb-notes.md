# seekdb 混合检索笔记

seekdb 的 `HYBRID_SEARCH` 是一个表函数：`SELECT * FROM HYBRID_SEARCH('<表名>', '<json dsl>')`。
一条 SQL 里同时包含全文臂与向量臂，融合在库内的 RRF 节点完成，结果自带 `_score`。

全文索引使用 `FULLTEXT INDEX idx_fts(document) WITH PARSER ngram`，中文按字片段匹配。
向量索引使用 `VECTOR INDEX idx_vec(embedding) WITH (DISTANCE=l2, TYPE=hnsw, LIB=vsag)`，
表必须是 `ORGANIZATION = HEAP`。

产品型号 OB-4.6.0 的发布说明只出现在这一段，适合做「关键词命中、纯语义不命中」的回归。
