#!/usr/bin/env bash
# 重置 s1 样例的运行产物并重建索引。
# 不修改 git 历史；如需把 demo_project 退回改前状态，用 git revert 对应提交。
set -euo pipefail
cd "$(dirname "$0")/../.."

DB="${MMCTX_SEEKDB_PATH:-/mnt/wsl/mmctx-seekdb}"
echo "[reset] 删除 seekdb 数据目录: $DB"
if [ -d "$DB" ]; then rm -rf "$DB"; fi
echo "[reset] 删除 out/ 与 devlog/ 里的本样例产物"
rm -f out/s1-order-filter.json out/s1-order-filter.md
find devlog -maxdepth 1 -name '*.jsonl' -newer samples/s1-order-filter/task.yaml -delete 2>/dev/null || true

echo "[reset] 重建索引与上下文（需要 PowerContext 在跑）"
make import context
echo "[reset] 完成。若 demo_project 已被补丁改动，请先 git revert 对应提交。"
