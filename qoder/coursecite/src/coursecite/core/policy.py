"""检索策略参数。

★ 唯一的 top-k 定义处。判据 D（见 README「分层验收」）：
`grep -rn "DEFAULT_TOP_K\\s*=" core/ adapters/` 只能命中本文件一次。
适配器禁止自己写死数量（8/10/20/50），只准使用 Query.limit。
"""

DEFAULT_TOP_K = 8
