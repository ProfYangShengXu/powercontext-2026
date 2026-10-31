# 薄桥边界

Node 薄桥只做参数绑定与协议转换：不排序、不融合、不过滤、不做 top-k 截断。
检索结果按数据库返回的顺序原样写回；截断由 SQL 的 `size` 在 seekdb 里完成。

导入请求体只有 `{id, collection, text, vector[512], metadata}` —— 向量已经在里面，
桥的依赖里没有 onnx / transformers，也没有 /embed 路由。

段落 id 由「知识库 + 来源文件 + 段序号」决定，写入走 upsert，所以重复导入是覆盖不是新增。
索引刷新调用 `dbms_index_manager.refresh`，再做一次可见性探测；刷新返回成功不等于新向量已可见。
