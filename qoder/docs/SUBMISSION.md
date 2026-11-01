# 提交清单 — Qoder 赛道（20 分）

对照官方要求逐项核对。**评审认平台记录的固定提交**：本地改完没推没提等于没做。

2026-11-01：站点已发布并设为公开（匿名访问 200）；待办只剩 UID/B5/B6 材料、推送 `qoder`、平台表单与 D 项回读。

## A. 平台表单（登录大赛测试平台 → Qoder 赛题 → 立即提测）

| # | 字段 | 填什么 | 完成前检查 |
|---|---|---|---|
| A1 | 项目网站网址 | `https://shiyi-study-0e8e9bd2mk9.qoder.zone` | 2026-11-01 匿名访问 200；可见范围=public |
| A2 | 代码仓库地址 | `https://github.com/ProfYangShengXu/powercontext-2026`（公开） | **需先推送 `qoder`**；评审能在 `qoder/studydesk/` 看到完整工程 |
| A3 | Qoder 账号 UID | 设置 → 个人资料 → 编辑 里复制 | 与 Sites 站点归属一致；待填 `dev-records/UID.txt` |
| A4 | 分支 / Commit | 分支固定 `qoder`；Commit 以提交时 C5 输出为准（本文件更新后 SHA 会变） | 基线自查 C1 通过 |
| A5 | 其余材料上传 | dev-records / 运行说明 / 验证样例 | 见 B |

## B. 仓库内材料（`qoder/`）

- [x] B1 `studydesk/` 完整源码，评审按 README 能从零跑通
- [x] B2 运行说明：环境、依赖版本、seekdb/PowerContext 部署接入、启动步骤、模型种子（`studydesk/README.md`）
- [x] B3 验证样例：`scripts/run_verify.sh` 全绿 + README 手动逐步版（含初始数据/输入/步骤/预期）；留痕 `dev-records/M4-verify/verify.log`
- [x] B4 技术结合证据：`dev-records/M4-verify/search_sample.jsonl`（seekdb 检索记录）与 `memory_sample.jsonl`（PowerContext 读写记录）
- [ ] B5 `dev-records/`：M0-M5 每阶段对话截图/录屏 + **Qoder 账号 UID 写在 dev-records/UID.txt**（M2–M5 留痕已入库，UID 与部分阶段录屏待补）
- [ ] B6 使用效果材料（鼓励）：Sites 访问统计截图、同学试用记录、提效对比
- [x] B7 复用开源代码已注明来源与改动（`studydesk/README.md`「依赖与来源」）

## 发布与留痕（手动）

1. [x] 站点已创建并发布，源目录 `qoder/studydesk/site/`。
2. [x] 可见范围已设为「任何人都可访问」（public）。
3. [x] 匿名访问返回 200；线上实拍存于 `dev-records/M5-site/site-live.png`。
4. [ ] 截图 Sites 发布设置页（画面需包含可见范围选项），保存为 `dev-records/M5-site/sites-publish-settings.png`。
5. [x] 完整 URL 已填入上表 A1；[ ] UID 填入 `dev-records/UID.txt`。
6. [ ] 推送 `qoder` 到 `ProfYangShengXu/powercontext-2026`，使 A2 的仓库与站内 README 链接可访问。

## C. 提交前自查（逐条执行）

```bash
cd <仓库根目录>

# C1 基线：输出 OK 才能提交
git fetch r 2>/dev/null; git fetch origin 2>/dev/null
git merge-base --is-ancestor r/2026-competition-ecosystem HEAD && echo OK || echo FAIL

# C2 没有真实凭据（必须无输出；跳过被忽略的运行目录）
grep -rniE "sk-[a-zA-Z0-9]{20,}|api[_-]?key *= *[\"'][^\"']{16,}|password *= *[\"'][^\"']+|BEGIN (RSA|OPENSSH) PRIVATE KEY" qoder/ \
  --exclude-dir=dev-records --exclude-dir=.venv --exclude-dir=data --exclude-dir=logs --exclude-dir=__pycache__

# C3 没有本地绝对路径泄漏到交付文件（必须无输出；跳过被忽略的运行目录）
grep -rniE "C:\\\\Users|/home/[a-z]+/|/Users/" qoder/studydesk/ qoder/docs/ qoder/scripts/ \
  --exclude=SUBMISSION.md --exclude-dir=.venv --exclude-dir=data --exclude-dir=logs --exclude-dir=__pycache__

# C4 Sites 可访问性（人工）：无痕窗口打开 A1 的网址，能看到项目页
# C5 要填到平台的两个值
echo "SHA: $(git rev-parse HEAD)"; echo "Branch: $(git rev-parse --abbrev-ref HEAD)"
```

2026-11-01 自查记录：C1 `OK`；C2、C3 无输出；忽略规则覆盖 `data/ logs/ .env __pycache__/`；C4 匿名访问站点返回 200（public）。

## D. 提交后

- [ ] D1 回读平台提交状态（别只看按钮变灰）
- [ ] D2 确认显示的 SHA/分支与 C5 一致
- [ ] D3 **评审结束前不暂停/不删除 Sites 站点**；改站内容后必须再点一次「发布」
- [ ] D4 持续收集使用效果数据，材料进 `qoder/docs/impact/`，再次提测前重新走 C

## 归零风险对照（赛题原文）

| 风险 | 触发 | 本项目对策 |
|---|---|---|
| 仅用 Qoder 发站，开发在别处做 | 验收③不过 | 主要编码必须在 Qoder 内完成，dev-records 留痕 |
| 截图替代源码 | 验收②不过 | B1 源码齐全 + B3 一键脚本 |
| 依赖本地未交付数据/状态 | 验收②不过 | sample_data 入库；脚本从零建库 |
| 技术结合核对不了 | 验收④不过 | B4 双日志落盘且入库 |
| 真实凭据入库 | 通用红线 | C2 自查；配置占位值 |
| 站点评审期间失效 | 通用红线 | D3 |
