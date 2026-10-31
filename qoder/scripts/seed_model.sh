#!/usr/bin/env bash
# 下载 seekdb 默认嵌入模型 (all-MiniLM-L6-v2, ONNX, ~90MB) 到 pyseekdb 缓存目录。
#
# 为什么不用 pip 默认下载：pyseekdb 默认走 hf-mirror.com，而 hf-mirror 会把
# /resolve/ 请求 308 重定向回 huggingface.co（大陆网络通常无法直连）。
# 本脚本改从 ModelScope（阿里魔搭）下载同样的文件。
set -euo pipefail

DEST="$HOME/.cache/pyseekdb/onnx_models/all-MiniLM-L6-v2/onnx"
BASE="https://modelscope.cn/models/AI-ModelScope/all-MiniLM-L6-v2/resolve/master"

FILES=(model.onnx tokenizer.json config.json special_tokens_map.json tokenizer_config.json vocab.txt)

mkdir -p "$DEST"
for f in "${FILES[@]}"; do
  if [ -s "$DEST/$f" ]; then
    echo "[skip] $f 已存在"
    continue
  fi
  echo "[get ] $f"
  curl -fL --retry 3 --max-time 600 -o "$DEST/$f" "$BASE/$f"
done

echo
echo "校验："
ls -lh "$DEST"
# model.onnx 应约 90MB；vocab.txt 应有 30522 行
lines=$(wc -l < "$DEST/vocab.txt")
if [ "$lines" -ne 30522 ]; then
  echo "警告：vocab.txt 行数 $lines != 30522，下载可能不完整" >&2
  exit 1
fi
echo "模型种子完成：$DEST"
