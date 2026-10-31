# S-04 发布与版本

## 版本号
demo_project 自身不单独发版，版本随仓库提交号走。发布记录只需要 git 提交 SHA，不需要额外的版本文件。

## 打包
demo_project 不打包成 wheel，也不发布到包索引；它只作为被修改的示例工程随仓库交付。

## 变更记录
每个改动由 git 提交信息描述，不维护独立的 CHANGELOG 文件。提交信息使用 Conventional Commits 前缀（feat/fix/test/docs/chore）。
