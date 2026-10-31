#!/usr/bin/env bash
# S1-S5 一键验证。要求：依赖已安装、嵌入模型已种子、PowerContext 已启动。
set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_URL="http://127.0.0.1:8001"
PC_URL="${POWERCONTEXT_BASE_URL:-http://127.0.0.1:8000}"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"
MODEL_DIR="$HOME/.cache/pyseekdb/onnx_models/all-MiniLM-L6-v2/onnx"
TMP_DIR="$(mktemp -d)"
APP_LOG="$TMP_DIR/app.log"
APP_PID=""
MARKER="脚本验收-$(date +%s)"
DOC_ID=""

pass_count=0
fail_count=0

pass() {
  pass_count=$((pass_count + 1))
  echo "PASS S$1 · $2"
}

fail() {
  fail_count=$((fail_count + 1))
  echo "FAIL S$1 · $2" >&2
  exit 1
}

stop_app() {
  if [ -n "${APP_PID:-}" ] && kill -0 "$APP_PID" 2>/dev/null; then
    kill "$APP_PID" 2>/dev/null || true
    for _ in $(seq 1 50); do
      if ! kill -0 "$APP_PID" 2>/dev/null; then
        break
      fi
      sleep 0.1
    done
    if kill -0 "$APP_PID" 2>/dev/null; then
      kill -9 "$APP_PID" 2>/dev/null || true
    fi
    wait "$APP_PID" 2>/dev/null || true
  fi
  APP_PID=""
  for _ in $(seq 1 30); do
    if ! curl -fsS "$BASE_URL/" >/dev/null 2>&1; then
      break
    fi
    sleep 0.1
  done
}

cleanup() {
  stop_app >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

wait_for_app() {
  for _ in $(seq 1 100); do
    if curl -fsS "$BASE_URL/" >/dev/null 2>&1; then
      return 0
    fi
    if [ -n "${APP_PID:-}" ] && ! kill -0 "$APP_PID" 2>/dev/null; then
      return 1
    fi
    sleep 0.2
  done
  return 1
}

start_app() {
  (cd "$PROJECT_DIR" && exec "$PYTHON_BIN" -m app.main) >>"$APP_LOG" 2>&1 &
  APP_PID=$!
  wait_for_app
}

echo "拾遗 S1-S5 验证 · $(date '+%F %T')"
echo "项目: $PROJECT_DIR"
echo

if curl -fsS "$BASE_URL/" >/dev/null 2>&1; then
  echo "FAIL PRE · $BASE_URL 已被占用，请先停止现有应用再运行验证" >&2
  exit 1
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "FAIL PRE · 找不到 Python：$PYTHON_BIN（请先按 README 创建 .venv）" >&2
  exit 1
fi

for model_file in model.onnx tokenizer.json config.json special_tokens_map.json tokenizer_config.json vocab.txt; do
  if [ ! -s "$MODEL_DIR/$model_file" ]; then
    echo "FAIL PRE · 嵌入模型缺少 $MODEL_DIR/$model_file，请先运行 scripts/seed_model.sh" >&2
    exit 1
  fi
done

if ! "$PYTHON_BIN" -c "import fastapi, pyseekdb, pypdf, uvicorn" >/dev/null 2>&1; then
  echo "FAIL PRE · Python 依赖未安装，请先执行 uv pip install --python .venv/bin/python -r requirements.txt" >&2
  exit 1
fi

echo "== S1 启动应用 =="
if ! start_app; then
  echo "--- 应用启动日志 ---" >&2
  tail -n 30 "$APP_LOG" >&2 || true
  fail 1 "应用未能在 $BASE_URL 启动"
fi
pass 1 "服务已启动 $BASE_URL"

echo
echo "== S2 导入 sample_data =="
INGEST_CODE=$(curl -sS -o "$TMP_DIR/ingest.json" -w '%{http_code}' \
  -X POST "$BASE_URL/ingest" \
  -H 'Content-Type: application/json' \
  -d '{"paths":["sample_data"],"course":"db"}')
if [ "$INGEST_CODE" != "200" ]; then
  fail 2 "HTTP $INGEST_CODE：$(head -c 300 "$TMP_DIR/ingest.json" 2>/dev/null || true)"
fi
S2_INFO=$("$PYTHON_BIN" - "$TMP_DIR/ingest.json" <<'PY' 2>&1
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
files = data.get("files") or []
bad = [item for item in files if item.get("status") != "ok"]
if data.get("total_chunks", 0) <= 0 or len(files) != 3 or bad or not data.get("ok"):
    raise SystemExit(
        f"total_chunks={data.get('total_chunks')} files={len(files)} bad={len(bad)}"
    )
print(f"total_chunks={data['total_chunks']} files={len(files)}")
PY
) || fail 2 "导入结果不合格：$S2_INFO"
pass 2 "$S2_INFO"

echo
echo "== S3 混合检索「写放大」 =="
SEARCH_CODE=$(curl -sS -o "$TMP_DIR/search.json" -w '%{http_code}' \
  --get "$BASE_URL/search" --data-urlencode 'q=写放大')
if [ "$SEARCH_CODE" != "200" ]; then
  fail 3 "HTTP $SEARCH_CODE：$(head -c 300 "$TMP_DIR/search.json" 2>/dev/null || true)"
fi
S3_INFO=$("$PYTHON_BIN" - "$TMP_DIR/search.json" "$TMP_DIR/doc_id" <<'PY' 2>&1
import json
import sys

items = json.load(open(sys.argv[1], encoding="utf-8"))
if not isinstance(items, list) or not items:
    raise SystemExit("检索结果为空")
top = items[0]
text = top.get("text") or ""
source = top.get("source_file") or ""
doc_id = top.get("doc_id") or ""
if "写放大" not in text:
    raise SystemExit(f"首条结果不含「写放大」：source={source}")
if not doc_id:
    raise SystemExit("首条结果缺少 doc_id")
open(sys.argv[2], "w", encoding="utf-8").write(doc_id)
print(f"top={source} distance={top.get('distance')} result_count={len(items)}")
PY
) || fail 3 "检索验收失败：$S3_INFO"
DOC_ID=$(cat "$TMP_DIR/doc_id")
pass 3 "$S3_INFO"

echo
echo "== S4 写入薄弱点反馈 =="
if ! curl -fsS "$PC_URL/health/ready" >/dev/null 2>&1; then
  fail 4 "PowerContext 未就绪（$PC_URL），请先运行 powercontext server run"
fi
"$PYTHON_BIN" - "$DOC_ID" "$MARKER" "$TMP_DIR/feedback_request.json" <<'PY'
import json
import sys

payload = {
    "doc_id": sys.argv[1],
    "verdict": "weak",
    "note": f"{sys.argv[2]}：写放大总记混",
    "query": "写放大",
}
with open(sys.argv[3], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False)
PY
FEEDBACK_CODE=$(curl -sS -o "$TMP_DIR/feedback.json" -w '%{http_code}' \
  -X POST "$BASE_URL/feedback" \
  -H 'Content-Type: application/json' \
  --data-binary @"$TMP_DIR/feedback_request.json")
if [ "$FEEDBACK_CODE" != "200" ]; then
  fail 4 "HTTP $FEEDBACK_CODE：$(head -c 300 "$TMP_DIR/feedback.json" 2>/dev/null || true)"
fi
S4_INFO=$("$PYTHON_BIN" - "$TMP_DIR/feedback.json" "$TMP_DIR/artifact" <<'PY' 2>&1
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
artifact = data.get("artifact") or {}
if not data.get("ok") or not data.get("memory_written") or not artifact.get("artifact_id"):
    raise SystemExit(f"ok={data.get('ok')} memory_written={data.get('memory_written')}")
ref = data.get("artifact_ref") or (
    f"{artifact.get('family')}/{artifact.get('artifact_id')}@{artifact.get('revision')}"
)
source_id = (data.get("source") or {}).get("source_id") or ""
open(sys.argv[2], "w", encoding="utf-8").write(ref)
print(f"artifact={ref} source={source_id}")
PY
) || fail 4 "反馈没有返回 artifact：$S4_INFO"
ARTIFACT=$(cat "$TMP_DIR/artifact")
pass 4 "$S4_INFO"

echo
echo "== S5 重启应用并读取复习建议 =="
stop_app
if ! start_app; then
  echo "--- 应用重启日志 ---" >&2
  tail -n 30 "$APP_LOG" >&2 || true
  fail 5 "应用重启失败"
fi
REVIEW_CODE=$(curl -sS -o "$TMP_DIR/review.json" -w '%{http_code}' "$BASE_URL/review")
if [ "$REVIEW_CODE" != "200" ]; then
  fail 5 "HTTP $REVIEW_CODE：$(head -c 300 "$TMP_DIR/review.json" 2>/dev/null || true)"
fi
S5_INFO=$("$PYTHON_BIN" - "$TMP_DIR/review.json" <<'PY' 2>&1
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
status = data.get("status")
suggestions = data.get("suggestions") or []
texts = [str(item.get("text") or "") for item in suggestions]
if status != "ready":
    raise SystemExit(f"status={status} suggestions={len(suggestions)}")
if not any("写放大" in text for text in texts):
    raise SystemExit(f"复习建议未包含知识点「写放大」，suggestions={len(suggestions)}")
hit = next(text for text in texts if "写放大" in text)
print(f"status=ready suggestions={len(suggestions)} knowledge_point=写放大")
print(f"hit={hit[:140]}")
PY
) || fail 5 "重启后复习建议不合格：$S5_INFO"
pass 5 "$S5_INFO"

echo
MEMORY_LOG=$(ls -t "$PROJECT_DIR"/logs/memory_*.jsonl 2>/dev/null | head -n 1)
if [ -n "$MEMORY_LOG" ]; then
  OPS=$("$PYTHON_BIN" - "$MEMORY_LOG" <<'PY' 2>&1
import json
import sys

lines = [line for line in open(sys.argv[1], encoding="utf-8").read().splitlines() if line.strip()]
ops = [json.loads(line).get("operation") for line in lines[-2:]]
print(",".join(str(op) for op in ops))
PY
  ) || OPS="读取失败"
  echo "日志尾操作: $OPS"
else
  echo "日志尾操作: 未找到 memory_*.jsonl"
fi

echo
echo "结果: $pass_count PASS / $fail_count FAIL"
echo "本次标记: $MARKER"
echo "artifact: $ARTIFACT"
echo "日志: $PROJECT_DIR/logs/"
