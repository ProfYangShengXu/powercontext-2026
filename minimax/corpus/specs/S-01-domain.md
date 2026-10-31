# S-01 订单领域模型

## 适用范围
本规格描述 demo_project 中订单（Order）的字段、状态取值与会员等级。所有业务代码与测试都以本文件为准，不得自行新增字段或状态。

## 字段
Order 是 frozen dataclass，包含四个字段：order_id（字符串，业务主键）、status（订单状态）、member_level（会员等级）、amount_cents（商品原价，单位：分，整数）。amount_cents 不含折扣，应付金额由 shop.pricing.total_cents 计算。

## 订单状态
状态只有四个合法取值：pending、paid、shipped、cancelled。它们也以常量和 STATUSES 元组的形式定义在 shop.models 中。任何筛选参数里出现这四个之外的状态都视为非法输入。

## 会员等级与折扣
会员等级只有四个合法取值：regular、silver、gold、platinum，定义在 MEMBER_LEVELS 元组中。对应折扣千分数分别是 0、50、100、150，即 0%、5%、10%、15%。应付金额 = amount_cents × (1000 - 折扣千分数) ÷ 1000，并按 half-up 规则四舍五入到整分。
