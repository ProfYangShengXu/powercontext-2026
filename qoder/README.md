# qoder/ — Build with Qoder 赛道

> 「拾遗」：本地学习资料库 + 个性化复习助手。seekdb 管检索，PowerContext 管记忆。

## 目录

```
qoder/
├── README.md            ← 本文件（提交前重写为评审入口，见下方计划）
├── docs/
│   ├── PRD.md           产品需求：痛点/功能/双技术分工/接口形状/验证样例/风险
│   ├── QODER-PROMPTS.md 分阶段开发提示词（M0-M5），在 Qoder 里按序使用
│   └── ENV-NOTES.md     本机环境验证记录 + 已踩坑（README 事实来源）
├── scripts/
│   └── seed_model.sh    嵌入模型种子（ModelScope 下载，已实测）
└── dev-records/         （开发中填充）Qoder 各阶段对话截图/录屏 + 账号 UID
```

## 当前状态

- [x] 题面/评审要求调研完毕（官方训练营平台 + 语雀详情页）
- [x] seekdb embedded 与 PowerContext 1.4.1 本机部署验证通过（含跨重启记忆复用）
- [x] PRD、提示词包、模型种子脚本就绪
- [ ] **M1-M5：在 Qoder 中开发主功能**（按 QODER-PROMPTS.md 阶段推进）
- [ ] 运行说明 + 验证样例成稿（README 重写）
- [ ] Qoder Sites 发布展示站（可见范围「任何人都可访问」）
- [ ] 提交平台填表（网址 / 仓库 / 分支 commit / UID）+ 自查

## 提交材料的落位约定

| 材料 | 位置 | 状态 |
|---|---|---|
| 项目网站网址 | 平台表单 | 待 M5 |
| 代码仓库 | 本仓库 `qoder/studydesk/`（Qoder 开发的主工程） | 待 M1 |
| 运行说明 | `qoder/studydesk/README.md` | M4 成稿 |
| 验证样例 | `qoder/studydesk/scripts/run_verify.sh` + README 操作步骤 | M4 |
| Qoder 开发记录 | `qoder/dev-records/`（含账号 UID） | 各阶段随手留 |
| 使用效果 | `qoder/docs/impact/`（访问统计/反馈，鼓励项） | 发布后收集 |
