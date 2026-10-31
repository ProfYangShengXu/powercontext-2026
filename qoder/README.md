# qoder/ — Build with Qoder 赛道

> 「拾遗」：本地学习资料库 + 个性化复习助手。seekdb 管检索，PowerContext 管记忆。

## 目录

```
qoder/
├── README.md            ← 本文件（赛道入口）
├── docs/
│   ├── PRD.md           产品需求：痛点/功能/双技术分工/接口形状/验证样例/风险
│   ├── QODER-PROMPTS.md 分阶段开发提示词（M0-M5），在 Qoder 里按序使用
│   ├── SUBMISSION.md    提交清单与自查
│   └── ENV-NOTES.md     本机环境验证记录 + 已踩坑（README 事实来源）
├── scripts/
│   └── seed_model.sh    嵌入模型种子（ModelScope 下载，已实测）
├── studydesk/           主工程：FastAPI + seekdb + PowerContext + 单页 UI
│   └── site/            M5 静态展示站（Qoder Sites 发布物料）
└── dev-records/         Qoder 各阶段对话截图/录屏 + 验证输出 + 账号 UID
```

## 当前状态

- [x] 题面/评审要求调研完毕（官方训练营平台 + 语雀详情页）
- [x] seekdb embedded 与 PowerContext 1.4.1 本机部署验证通过（含跨重启记忆复用）
- [x] PRD、提示词包、模型种子脚本就绪
- [x] **M1–M4：主功能、单页 UI、PowerContext 记忆闭环、一键验收**（5 PASS / 0 FAIL）
- [x] M5 展示站源码与本地预览（`studydesk/site/`，桌面/移动端布局自检通过）
- [ ] Qoder Sites 发布展示站（手动操作，可见范围「任何人都可访问」）
- [ ] 提交平台填表（网址 / 仓库 / 分支 commit / UID）+ 自查

## 提交材料的落位约定

| 材料 | 位置 | 状态 |
|---|---|---|
| 项目网站网址 | 平台表单 | 待发布后填写 |
| 代码仓库 | 本仓库 `qoder/studydesk/`（Qoder 开发的主工程） | 源码完成，待推送 |
| 展示站 | `qoder/studydesk/site/`（静态单页） | 已完成，待发布 |
| 运行说明 | `qoder/studydesk/README.md` | 已完成 |
| 验证样例 | `qoder/studydesk/scripts/run_verify.sh` + README 操作步骤 | 已完成 |
| Qoder 开发记录 | `qoder/dev-records/`（含账号 UID） | 持续补充 |
| 使用效果 | `qoder/docs/impact/`（访问统计/反馈，鼓励项） | 发布后收集 |
