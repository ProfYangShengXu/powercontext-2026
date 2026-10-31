# 样例 s1-order-filter 的期望来源（人读版）

任务：实现 `demo_project/shop/filters.py` 的 `filter_orders`，让
`demo_project/tests/test_filters.py` 从失败变为全绿，且 `test_pricing.py` 保持全绿。

## 上下文包里必须出现的来源

| 类别 | 路径 | 为什么必要 |
|---|---|---|
| 规格 | `corpus/specs/S-02-filtering.md` | 四个条件的 AND 语义、金额比较、空集合与非法值 |
| 规格 | `corpus/specs/S-01-domain.md` | 状态/会员等级枚举与折扣千分数 |
| 规格 | `corpus/specs/S-03-testing.md` | 测试文件不可修改、改前红必须是断言失败 |
| 代码 | `demo_project/shop/filters.py` | 被修改的目标；当前是占位实现 |
| 代码 | `demo_project/shop/pricing.py` | `total_cents` 是「应付金额」的唯一来源 |
| 代码 | `demo_project/shop/models.py` | `STATUSES` / `MEMBER_LEVELS` 是合法枚举的唯一来源 |
| 测试 | `demo_project/tests/test_filters.py` | 目标测试，验收依据 |
| 测试 | `demo_project/tests/test_pricing.py` | 回归测试 |

## 怎么核对

```bash
make context
python - <<'PY'
import json, yaml
pack = json.load(open("out/s1-order-filter.json"))
expected = yaml.safe_load(open("samples/s1-order-filter/task.yaml"))["expected_sources"]
paths = {s["path"] for s in pack["sources"]}
missing = [e["path"] for e in expected if e["path"] not in paths]
print("missing:", missing)
assert not missing, missing
PY
```

机器判据有两层：`cmd_context` 出包时直接校验 `expected_sources`（缺一条即 exit 3），
`tests/test_sample_expected.py` 另外从真实索引复算一遍，并断言每条来源的 `chunk_uid`
都能在 seekdb 里回查。当前包 12 条来源：spec 4 / code 6 / test 2（`docs/context-s1.log`）。

## 改前 / 改后

- 改前：`pytest` 输出 `9 failed, 6 passed`，失败原因是 AssertionError / pytest 断言失败。
- 改后：`pytest` 输出 `15 passed`，回归测试不变红。
