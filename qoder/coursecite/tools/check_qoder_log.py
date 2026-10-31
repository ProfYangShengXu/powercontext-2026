#!/usr/bin/env python3
"""审计 Qoder 开发记录与代码是否对得上（文档 §8.4 的可执行版）。

三档判据，全绿才退出码 0：
  1. 每个触及 src/*.py 的 commit 要么带 `Qoder-Quest:` trailer，要么 message 以 `manual:` 开头。
  2. docs/qoder-log/index.md 里每个 Quest ID 至少对应一个真实 commit（git log --grep）。
  3. src/ 下每个文件至少被一个合规 commit 碰过。

用法：
    python tools/check_qoder_log.py            # 在 qoder/coursecite 下
    python tools/check_qoder_log.py --repo /path/to/qoder
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
QUEST_RE = re.compile(r"Qoder-Quest:\s*(\S+)")
MANUAL_PREFIX = "manual:"


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git {' '.join(args)} 失败")
    return proc.stdout


def commits_touching(repo: Path, prefix: str) -> list[tuple[str, str]]:
    """返回 [(hash, message)]，只保留改动过 prefix 路径的 commit。"""
    out = git(repo, "log", "--format=%H%x1f%B%x1e", "--", prefix)
    commits: list[tuple[str, str]] = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, _, message = record.partition("\x1f")
        commits.append((sha.strip(), message.strip()))
    return commits


def parse_index_quests(path: Path) -> list[str]:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    return sorted(set(QUEST_RE.findall(text)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=str(PROJECT))
    args = parser.parse_args()
    repo = Path(args.repo)
    src_dir = repo / "src"

    try:
        git(repo, "rev-parse", "--show-toplevel")
    except RuntimeError as exc:
        print(f"[FAIL] 不是 git 仓库：{exc}")
        return 2

    errors: list[str] = []
    src_commits = commits_touching(repo, "src")
    if not src_commits:
        errors.append("没有任何 commit 改动过 src/（Qoder 开发证据链为空）")

    compliant: list[str] = []
    for sha, message in src_commits:
        quest = QUEST_RE.search(message)
        if quest:
            compliant.append(sha)
            continue
        if message.startswith(MANUAL_PREFIX):
            print(f"[WARN] manual commit {sha[:9]}：人工改动，需在 index.md 人工改动表登记")
            compliant.append(sha)
            continue
        errors.append(f"commit {sha[:9]} 改动了 src/ 但没有 Qoder-Quest trailer，也不是 manual:")

    index_path = repo / "docs" / "qoder-log" / "index.md"
    index_quests = parse_index_quests(index_path)
    if not index_quests:
        errors.append("docs/qoder-log/index.md 里没有任何 Quest ID")
    for quest in index_quests:
        if not git(repo, "log", "--all", "--grep", re.escape(quest), "--format=%H").strip():
            errors.append(f"index.md 记录的 Quest {quest} 在 git log 里找不到对应 commit")

    src_files = sorted(
        str(p.relative_to(repo)) for p in src_dir.rglob("*.py") if p.is_file()
    ) if src_dir.is_dir() else []
    for rel in src_files:
        touched = bool(git(repo, "log", "--format=%H", "--", rel).strip())
        if not touched:
            errors.append(f"{rel} 从未被任何 commit 碰过")

    print(f"src 文件数：{len(src_files)}；改动过 src 的 commit：{len(src_commits)}；"
          f"index Quest：{len(index_quests)}")
    if errors:
        print("\n[FAIL] 审计未通过：")
        for item in errors:
            print(f"  - {item}")
        print("\n提示：本仓库若尚未用 Qoder 重做，这是预期结果；见 README「还差哪一步」。")
        return 1
    print("\n[PASS] 三档判据全绿。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
