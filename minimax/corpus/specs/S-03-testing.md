# S-03 测试与提交约定

## 测试文件
demo_project/tests 下的测试文件是验收依据，任何实现改动都不得修改测试文件、pytest.ini 或测试数据。自动化流程会拒绝触及这些路径的补丁。

## 失败语义
改前失败必须是断言失败（AssertionError 或 pytest 的断言类失败），不能是 ImportError、连接错误或环境错误。改后同一组测试必须全绿，回归测试也必须保持全绿。

## 命令
在 demo_project 目录执行 python -m pytest -q，或在仓库根目录执行 python -m pytest -q demo_project/tests。测试日志必须记录命令、退出码与对应的代码版本（git 提交 SHA）。

## 覆盖范围
目标测试覆盖筛选的四类条件、组合条件、空集合与非法值；回归测试覆盖会员折扣与应付金额计算的既有行为。
