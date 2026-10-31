# S-02 订单筛选

## 接口
filter_orders(orders, *, statuses=None, min_total_cents=None, member_levels=None, exclude_ids=None) -> list[Order]。函数是纯函数：不得修改入参 orders，也不得修改其中的 Order 对象；必须返回一个新的 list。

## 筛选条件
四个条件全部为可选参数，默认 None 表示该条件不生效。多个条件之间是逻辑与（AND）：一条订单必须同时满足全部已给出的条件才会被返回。statuses 与 member_levels 是字符串集合；min_total_cents 是整数下限；exclude_ids 是要排除的 order_id 集合。

## 金额比较
min_total_cents 比较的是订单的应付金额（即 shop.pricing.total_cents(order) 的结果），不是 amount_cents 原价。比较使用大于等于：应付金额 ≥ min_total_cents 即满足条件。这样会员折扣会真实影响筛选结果。

## 集合空值与非法值
statuses 或 member_levels 传空集合时，没有任何订单满足该条件，结果必须为空列表；它们与参数为 None（不筛选）语义不同。集合里出现合法枚举之外的取值时，必须抛 ValueError，不允许静默忽略。

## 顺序与去重
结果保持输入 orders 的原有顺序，不排序、不去重。exclude_ids 里的 order_id 即使不在 orders 中也应被安全忽略。

## 可观测性
筛选是纯内存计算，不读写文件、不发网络请求、不写日志。调用方通过返回值判断结果。
