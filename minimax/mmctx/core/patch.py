"""回复解析与补丁守卫：模型只能改业务代码，不许碰测试。"""

from __future__ import annotations

import re

_FENCE = re.compile(r"```(?:diff|patch)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_PY_FENCE = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_THINK = re.compile(r"<think\b.*?</think\s*>", re.DOTALL | re.IGNORECASE)
_FILE_HEADER = re.compile(r"^(?:\+\+\+|---)\s+(?:[ab]/)?(.+?)(?:\t.*)?$")
_HUNK = re.compile(r"^@@ ", re.MULTILINE)

FORBIDDEN_PARTS = ("/tests/", "tests/", "pytest.ini", "conftest.py", "tox.ini")
ALLOWED_PREFIX = "demo_project/shop/"


def extract_diff(reply: str) -> str:
    """从模型回复里取出 unified diff；去掉 think 段，接受带/不带围栏。"""
    text = _THINK.sub("", reply or "")
    blocks = [match.group(1).strip("\n") for match in _FENCE.finditer(text)]
    blocks = [block for block in blocks if "--- " in block and "+++ " in block]
    if blocks:
        return max(blocks, key=len)
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith("diff --git ") or line.startswith("--- a/"):
            start = index
            break
    if start is None:
        return ""
    body = "\n".join(lines[start:])
    cut = body.find("\n```")
    return body[:cut] if cut >= 0 else body


def patch_paths(diff: str) -> list[str]:
    """提取补丁触及的文件（去掉 a/ b/ 前缀，去重保序）。"""
    paths: list[str] = []
    for line in diff.splitlines():
        if not (line.startswith("--- ") or line.startswith("+++ ")):
            continue
        match = _FILE_HEADER.match(line)
        if not match:
            continue
        path = match.group(1).strip()
        if path == "/dev/null":
            continue
        path = path.removeprefix("a/").removeprefix("b/")
        if path not in paths:
            paths.append(path)
    return paths


def guard_patch(diff: str, *, allowed_prefix: str = ALLOWED_PREFIX) -> tuple[list[str], list[str]]:
    """补丁守卫。返回 (触及文件, 拒绝理由)；理由非空即拒收。"""
    if not diff.strip():
        return [], ["model reply contains no unified diff"]
    if not _HUNK.search(diff):
        return [], ["diff has no hunks"]
    paths = patch_paths(diff)
    if not paths:
        return [], ["diff has no file headers"]
    errors: list[str] = []
    for path in paths:
        lowered = path.lower()
        if any(part in lowered for part in FORBIDDEN_PARTS):
            errors.append(f"{path}: patch must not modify tests or test configuration")
        elif not path.startswith(allowed_prefix):
            errors.append(f"{path}: patch must stay under {allowed_prefix}")
    return paths, errors


TARGET_FILE = "demo_project/shop/filters.py"


def extract_python_file(reply: str, *, marker: str = "def filter_orders(") -> str:
    """模型直接给出完整新文件时的提取；只接受包含目标函数、体积最大的 python 块。"""
    blocks = [match.group(1).strip("\n") + "\n" for match in _PY_FENCE.finditer(reply or "")]
    blocks = [block for block in blocks if marker in block]
    return max(blocks, key=len) if blocks else ""


def build_file_diff(path: str, old: str, new: str) -> str:
    """由旧文件与模型给出的新文件生成 unified diff（弱模型写整文件比写 diff 可靠）。"""
    import difflib

    if old == new:
        return ""
    lines = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    )
    return "".join(lines)
