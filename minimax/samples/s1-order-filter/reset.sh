#!/usr/bin/env bash
# 重置 s1 样例的运行产物并重建索引。
# - 只删 seekdb 数据目录（必须是 *mmctx-seekdb* 路径）与 out/ 的本样例产物；
# - 不删 devlog/（它是证据），不修改 git 历史。
# 如需把 demo_project 退回改前状态，用 `git revert <修复提交>`。
set -euo pipefail
cd "$(dirname "$0")/../.."

DB="${MMCTX_SEEKDB_PATH:-/mnt/wsl/mmctx-seekdb}"
case "$DB" in
  *mmctx-seekdb*) ;;
  *)
    echo "[reset] 拒绝删除：MMCTX_SEEKDB_PATH=$DB 不像 mmctx 专用目录" >&2
    exit 2
    ;;
esac

echo "[reset] 删除 seekdb 数据目录: $DB"
if [ -d "$DB" ]; then rm -rf -- "$DB"; fi

echo "[reset] 删除 out/ 的本样例产物"
rm -f -- out/s1-order-filter.json out/s1-order-filter.md

echo "[reset] 重建索引与上下文（需要 PowerContext 在跑：make serve）"
make import context

echo "[reset] 完成。devlog/ 与 docs/ 里的证据未删除。"
echo "[reset] 若 demo_project 已被补丁改动，先 git revert 对应的 fix(mmctx) 提交再重放。"
