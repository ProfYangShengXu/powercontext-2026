PY ?= $(CURDIR)/.venv/bin/python
MMCTX := $(PY) -m mmctx.cli
SAMPLE := samples/s1-order-filter/task.yaml
# 一次 demo 的所有步骤共用一个 run_id，devlog/<run_id>.jsonl 才会串起
# context → prompt → model reply → patch → apply → test → state 的完整证据链。
RUN_ID := $(shell date -u +%Y%m%dT%H%M%SZ)-demo
# 交付分支上最后一次真实 apply 的 run_id（make verify 默认重放它）
RUN ?= final4

.PHONY: help venv deps env init serve import context explain run test-before test-after state resume verify check test demo clean

help:
	@printf '%s\n' \
	  'make init        建 venv、装依赖、建索引表（PowerContext 未启动也能成功，只报 ready=false）' \
	  'make serve       前台启动 PowerContext Server（Basic Memory，另开一个终端）' \
	  'make import      扫描 demo_project 与 corpus/specs 写入 seekdb，并登记 PowerContext Source' \
	  'make context     组装上下文包到 out/s1-order-filter.{json,md}（含 expected_sources 校验）' \
	  'make explain     打印候选 SQL 的 EXPLAIN（应含 TEXT RETRIEVAL SCAN + VECTOR INDEX）' \
	  'make test-before 记录改前测试（目标测试应红，退出码 1 是预期）' \
	  'make run         调 MiniMax 生成补丁、应用并提交（只提交 demo_project/shop）' \
	  'make test-after  记录改后测试（应全绿）' \
	  'make state       把任务状态写进 PowerContext（自动对齐最近一次 apply 的 run_id/commit）' \
	  'make resume      新进程读回任务状态' \
	  'make verify      来源反查 + 按 devlog 重放补丁（默认 RUN=final4）' \
	  'make demo        一次跑完：test-before → import → context → run → test-after → state → resume → verify' \
	  'make test        跑本模块单元/集成测试（缺 PC/seekdb 的用例会 skip）' \
	  'PY=/path/to/python 可覆盖解释器；RUN_ID=xxx 可固定本次 demo 的编号'

venv:
	test -d .venv || python3 -m venv .venv

deps: venv
	.venv/bin/pip install -q -r requirements.txt

env:
	test -f .env || cp .env.example .env

init: deps env
	$(MMCTX) init

# Basic Memory 不需要额外模型 API；POWERCONTEXT_HOME 可用环境变量覆盖。
serve:
	MMCTX_PY=$(PY) ./scripts/serve_powercontext.sh

import:
	$(MMCTX) import --run-id $(RUN_ID)

context:
	$(MMCTX) context --run-id $(RUN_ID) --task $(SAMPLE)

explain:
	$(MMCTX) explain --task $(SAMPLE)

run:
	$(MMCTX) run --run-id $(RUN_ID) --task $(SAMPLE) --context out/s1-order-filter.json --commit

test-before:
	$(MMCTX) test --run-id $(RUN_ID) --label test-before --log docs/test-before.log

test-after:
	$(MMCTX) test --run-id $(RUN_ID) --label test-after --log docs/test-after.log

state:
	$(MMCTX) state --run-id $(RUN_ID) --task $(SAMPLE) --context out/s1-order-filter.json \
	  --status applied --todo "补丁已应用并通过目标测试" --todo "等待回归与验收"

resume:
	$(MMCTX) resume --run-id $(RUN_ID) --task $(SAMPLE)

verify:
	$(MMCTX) verify --run-id $(RUN_ID) --context out/s1-order-filter.json --run $(RUN)

check:
	$(MMCTX) check

test: deps
	$(PY) -m pytest -q tests

# test-before 的退出码 1 是预期结果，用 '-' 让 demo 继续；日志里仍记录真实退出码。
demo: init
	-$(MMCTX) test --run-id $(RUN_ID) --label test-before --log docs/test-before.log
	$(MMCTX) import --run-id $(RUN_ID)
	$(MMCTX) context --run-id $(RUN_ID) --task $(SAMPLE)
	$(MMCTX) run --run-id $(RUN_ID) --task $(SAMPLE) --context out/s1-order-filter.json --commit
	$(MMCTX) test --run-id $(RUN_ID) --label test-after --log docs/test-after.log
	$(MMCTX) state --run-id $(RUN_ID) --task $(SAMPLE) --context out/s1-order-filter.json \
	  --status applied --todo "补丁已应用并通过目标测试" --todo "等待回归与验收"
	$(MMCTX) resume --run-id $(RUN_ID) --task $(SAMPLE)
	$(MMCTX) verify --run-id $(RUN_ID) --context out/s1-order-filter.json --run $(RUN_ID)

# 只清缓存；out/ 与 devlog/ 是证据，不在这里删。
clean:
	rm -rf .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
