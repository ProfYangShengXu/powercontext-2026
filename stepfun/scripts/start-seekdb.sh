#!/usr/bin/env bash
# 启动本机 seekdb server（本项目用 server 模式；桥通过 seekdb-js 的 host/port 接入）。
#
# 用法：
#   SEEKDB_BIN=/path/to/seekdb ./scripts/start-seekdb.sh
#   SEEKDB_DATA_DIR=/var/lib/seekdb ./scripts/start-seekdb.sh
#
# 为什么脚本要检查文件系统：seekdb 初始化时对新库的 block file 调用 fallocate()，
# 而 WSL 的 Windows 盘挂载（v9fs/9p）与 FAT/exFAT/SMB 不支持；失败时进程直接退出，
# 日志是 "Fail to fallocate block file ... Operation not supported"。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${SEEKDB_PORT:-2881}"
DATA_DIR="${SEEKDB_DATA_DIR:-$ROOT/.seekdb-data}"
BIN="${SEEKDB_BIN:-$(command -v seekdb || true)}"

if [[ -z "$BIN" ]]; then
  cat >&2 <<'EOF'
找不到 seekdb 可执行文件。三种取得方式（任选其一）：

  1) 官方 Linux 安装脚本
     curl -fsSL https://obportal.s3.ap-southeast-1.amazonaws.com/download-center/opensource/seekdb/seekdb_install.sh | bash
  2) 从源码构建（较慢，产物在 build_release/src/observer/seekdb）
     git clone https://github.com/oceanbase/seekdb.git && cd seekdb && ./build.sh release --init --make
  3) 使用已有的构建产物
     SEEKDB_BIN=/绝对路径/seekdb ./scripts/start-seekdb.sh

官方镜像也提供 server 模式：oceanbase/seekdb:latest（本机未实测镜像拉取）。
EOF
  exit 1
fi

mkdir -p "$DATA_DIR"
FSTYPE="$(stat -f -c '%T' "$DATA_DIR" 2>/dev/null || echo unknown)"
case "$FSTYPE" in
  v9fs|9p|msdos|vfat|exfat|ntfs|cifs|smbfs)
    if [[ "${SEEKDB_ALLOW_UNSUPPORTED_FS:-0}" != "1" ]]; then
      cat >&2 <<EOF
！数据目录所在文件系统是 $FSTYPE，seekdb 需要 fallocate()，这里会启动失败。
  数据目录：$DATA_DIR

可选方案：
  A. 放到支持 fallocate 的 Linux 文件系统（ext4/xfs/btrfs），例如 WSL 的 ext4 路径；
  B. 临时放在 tmpfs（内存盘，重启即丢，只适合演示）：
       SEEKDB_DATA_DIR=/dev/shm/seekdb-data ./scripts/start-seekdb.sh
  C. 用 Docker，数据落 named volume：
       docker run -d --name seekdb -p 2881:2881 -v seekdb_data:/var/lib/oceanbase oceanbase/seekdb:latest
  D. 明确知道风险仍要继续：SEEKDB_ALLOW_UNSUPPORTED_FS=1 ./scripts/start-seekdb.sh
EOF
      exit 1
    fi
    echo "！警告：$FSTYPE 可能不支持 fallocate，按 SEEKDB_ALLOW_UNSUPPORTED_FS=1 继续。" >&2
    ;;
esac

if (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -qE "[:.]$PORT[[:space:]]"; then
  echo "！端口 $PORT 已被占用；换 SEEKDB_PORT 或先停掉旧进程。" >&2
  exit 1
fi

echo "seekdb: $BIN"
echo "数据目录: $DATA_DIR（$FSTYPE）"
echo "端口: $PORT"
echo "日志: $DATA_DIR/log/seekdb.log"
echo

# log_disk_size 最小 2G（源码 SERVER_MIN_LOG_DISK_SIZE），低于它启动会报
# OB_RESOURCE_UNIT_VALUE_INVALID。datafile 默认 512M 足够迷你知识库演示。
exec "$BIN" \
  --base-dir "$DATA_DIR" \
  --port "$PORT" \
  --nodaemon \
  --parameter "datafile_size=${SEEKDB_DATAFILE_SIZE:-512M}" \
  --parameter "log_disk_size=${SEEKDB_LOG_DISK_SIZE:-2G}"
