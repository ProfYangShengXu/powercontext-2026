# CourseCite（课引）

课程资料混合检索 + 引用定位 + 检索留痕。完整运行说明、验证样例与提交缺口见上一级
[`../README.md`](../README.md)。

最短路径（已有 seekdb server，且当前目录下的包已安装）：

```bash
export COURSECITE_STORE=live SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2881
coursecite ingest
coursecite search "上次讲 HNSW 是在第几讲"
```

该文件同时是 `pyproject.toml` 里 `readme = "README.md"` 的打包入口，勿删。
