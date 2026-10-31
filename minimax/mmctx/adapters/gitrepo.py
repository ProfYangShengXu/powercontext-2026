"""工作副本的 git 操作：应用补丁、取版本、提交。"""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _run(self, args: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args, cwd=self.root, input=stdin, capture_output=True, text=True, check=False
        )

    def head(self) -> str:
        result = self._run(["git", "rev-parse", "HEAD"])
        if result.returncode != 0:
            raise GitError(result.stderr.strip() or "git rev-parse failed")
        return result.stdout.strip()

    def status(self, paths: list[str] | None = None) -> str:
        """工作区状态；传 paths 时只看这些路径（避免 devlog/out 等证据文件干扰）。"""
        args = ["git", "status", "--porcelain"]
        if paths:
            args.extend(["--", *paths])
        return self._run(args).stdout.strip()

    def show(self, path: str, rev: str = "HEAD") -> str:
        result = self._run(["git", "show", f"{rev}:{path}"])
        if result.returncode != 0:
            raise FileNotFoundError(f"{rev}:{path}")
        return result.stdout

    def check_patch(self, diff_text: str) -> tuple[bool, str]:
        result = self._run(["git", "apply", "--check", "--whitespace=nowarn", "-"], stdin=diff_text)
        return result.returncode == 0, (result.stderr or result.stdout).strip()

    def apply_patch(self, diff_text: str) -> None:
        ok, message = self.check_patch(diff_text)
        if ok:
            result = self._run(["git", "apply", "--whitespace=nowarn", "-"], stdin=diff_text)
            if result.returncode != 0:
                raise GitError(result.stderr.strip() or "git apply failed")
            return
        # 补丁带 index 行时可用 3-way 做模糊匹配；仍然失败就整体拒绝（不留半个补丁）。
        result = self._run(["git", "apply", "--3way", "--whitespace=nowarn", "-"], stdin=diff_text)
        if result.returncode != 0:
            raise GitError(f"git apply 失败：{message}")

    def reverse_check(self, diff_text: str) -> tuple[bool, str]:
        result = self._run(["git", "apply", "--reverse", "--check", "--whitespace=nowarn", "-"], stdin=diff_text)
        return result.returncode == 0, (result.stderr or result.stdout).strip()

    def revert(self, paths: list[str]) -> None:
        """把指定路径恢复到 HEAD（补丁通过但目标测试失败时用）。"""
        if not paths:
            return
        result = self._run(["git", "checkout", "--", *paths])
        if result.returncode != 0:
            raise GitError(result.stderr.strip() or "git checkout failed")

    def commit(self, message: str, paths: list[str]) -> str:
        add = self._run(["git", "add", "--", *paths])
        if add.returncode != 0:
            raise GitError(add.stderr.strip() or "git add failed")
        result = self._run(["git", "commit", "-m", message])
        if result.returncode != 0:
            raise GitError(result.stderr.strip() or result.stdout.strip() or "git commit failed")
        return self.head()

    def diff_paths(self, rev: str = "HEAD") -> list[str]:
        result = self._run(["git", "show", "--name-only", "--pretty=format:", rev])
        return [line for line in result.stdout.splitlines() if line.strip()]
