"""入库规则：扫描 .md/.txt -> 按标题切段 -> 内容 hash 幂等。

纯标准库，便于在没有数据库的机器上单独测试切段与出处字段。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from .models import Chunk, Citation

SUPPORTED_SUFFIXES = {".md", ".txt"}

_HEADING = re.compile(r"^##\s+(.*\S)\s*$")
_SECTION = re.compile(r"^((?:\d+\.)*\d+)\s+(.*\S)\s*$")
_PAGE_MARK = re.compile(r"<!--\s*page\s*[:=]?\s*(\d+)\s*-->", re.IGNORECASE)
_LECTURE_FILE = re.compile(r"[Ll](\d{1,3})")


@dataclass
class IngestReport:
    root: str
    files: int = 0
    chunks: int = 0
    written: int = 0
    duplicates: int = 0
    total: int = 0
    skipped: list[str] = field(default_factory=list)
    courses: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "files": self.files,
            "chunks": self.chunks,
            "written": self.written,
            "duplicates": self.duplicates,
            "total": self.total,
            "skipped": list(self.skipped),
            "courses": list(self.courses),
        }


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def chunk_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()[:16]


def make_chunk_id(course: str, lecture: int, section: str, section_title: str, page: int | None, text: str) -> str:
    key = "\x1f".join(
        [course, str(lecture), section, section_title, "" if page is None else str(page), normalize_text(text)]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _parse_front_matter(lines: list[str]) -> tuple[dict[str, str], int]:
    """返回 (元数据, 正文起始下标)。只支持 `key: value` 的简单 front matter。"""
    meta: dict[str, str] = {}
    if not lines or lines[0].strip() != "---":
        return meta, 0
    for idx in range(1, len(lines)):
        raw = lines[idx].strip()
        if raw == "---":
            return meta, idx + 1
        if ":" in raw:
            key, value = raw.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, 0


def _meta_int(meta: dict[str, str], key: str) -> int | None:
    value = meta.get(key)
    if value is None or not re.fullmatch(r"\d+", value):
        return None
    return int(value)


def _derive_meta(path: Path, rel: str, meta: dict[str, str]) -> tuple[str, str, int]:
    course = meta.get("course") or path.parent.name
    course_name = meta.get("course_name") or course
    lecture = _meta_int(meta, "lecture")
    if lecture is None:
        match = _LECTURE_FILE.search(path.stem)
        lecture = int(match.group(1)) if match else 0
    return course, course_name, lecture


def _snippet(body: str, width: int = 180) -> str:
    compact = re.sub(r"\s+", " ", body).strip()
    if len(compact) <= width:
        return compact
    return compact[:width].rstrip() + "…"


def split_sections(
    rel_path: str,
    text: str,
    *,
    course: str,
    course_name: str,
    lecture: int,
) -> list[Chunk]:
    """按 `## ` 标题切段；`<!-- page: N -->` 更新当前页码。"""
    lines = text.splitlines()
    chunks: list[Chunk] = []
    current_section = ""
    current_title = ""
    current_page: int | None = None
    body: list[str] = []
    line_start = 1

    def flush() -> None:
        nonlocal body, current_section, current_title, current_page, line_start
        raw = "\n".join(body).strip()
        if not raw:
            body = []
            return
        section = current_section or "全文"
        title = current_title or "正文"
        chunks.append(
            Chunk(
                id=make_chunk_id(course, lecture, section, title, current_page, raw),
                text=raw,
                snippet=_snippet(raw),
                citation=Citation(
                    course=course,
                    course_name=course_name,
                    lecture=lecture,
                    section=section,
                    section_title=title,
                    page=current_page,
                    path=rel_path,
                    line_start=line_start,
                ),
                content_hash=chunk_hash(raw),
            )
        )
        body = []

    for line_no, line in enumerate(lines, start=1):
        heading = _HEADING.match(line)
        if heading:
            flush()
            heading_text = heading.group(1).strip()
            section_match = _SECTION.match(heading_text)
            if section_match:
                current_section, current_title = section_match.group(1), section_match.group(2)
            else:
                current_section, current_title = "", heading_text
            current_page = None
            line_start = line_no
            continue
        page_mark = _PAGE_MARK.search(line)
        if page_mark:
            current_page = int(page_mark.group(1))
            line_wo_mark = _PAGE_MARK.sub("", line).strip()
            if line_wo_mark:
                body.append(line_wo_mark)
            continue
        if not body and not line.strip():
            continue
        body.append(line)

    flush()
    return chunks


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def load_corpus(root: str | Path) -> tuple[list[Chunk], IngestReport]:
    root_path = Path(root)
    if not root_path.is_dir():
        raise FileNotFoundError(f"语料目录不存在：{root_path}")
    report = IngestReport(root=str(root_path))
    seen: set[str] = set()
    chunks: list[Chunk] = []
    courses: set[str] = set()

    for path in sorted(root_path.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        report.files += 1
        rel = path.relative_to(root_path).as_posix()
        text = _read_text(path)
        lines = text.splitlines()
        if path.suffix.lower() == ".md":
            meta, body_start = _parse_front_matter(lines)
        else:
            meta, body_start = {}, 0
        course, course_name, lecture = _derive_meta(path, rel, meta)
        body = "\n".join(lines[body_start:])
        file_chunks = split_sections(rel, body, course=course, course_name=course_name, lecture=lecture)
        if not file_chunks:
            report.skipped.append(f"{rel}: 没有可入库的段落")
            continue
        courses.add(course)
        for chunk in file_chunks:
            if chunk.id in seen:
                report.duplicates += 1
                continue
            seen.add(chunk.id)
            chunks.append(chunk)

    report.chunks = len(chunks)
    report.courses = sorted(courses)
    return chunks, report
