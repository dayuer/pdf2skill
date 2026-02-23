"""
术语表提取器 — Phase 5: Glossary Extraction

从 ValidatedSkill 集合中提取领域术语表，支持：
1. 括号标注式定义：术语（definition）
2. 判断句式定义：X 是指/定义为/称为 Y
3. 高频实体聚合
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import config
from .skill_validator import ValidatedSkill


@dataclass
class GlossaryEntry:
    """术语条目"""

    term: str
    definition: str
    source_skills: list[str] = field(default_factory=list)
    frequency: int = 1


def _extract_from_parenthetical(text: str) -> list[tuple[str, str]]:
    """提取括号标注式定义：术语（解释）"""
    results = []
    # 中文括号
    for m in re.finditer(r"([\u4e00-\u9fff]{2,10})（([^）]{4,60})）", text):
        results.append((m.group(1), m.group(2)))
    # 英文括号
    for m in re.finditer(r"([\u4e00-\u9fff]{2,10})\(([^)]{4,60})\)", text):
        results.append((m.group(1), m.group(2)))
    return results


def _extract_from_definition(text: str) -> list[tuple[str, str]]:
    """提取判断句式定义：X 是指/定义为 Y"""
    results = []
    patterns = [
        r"([\u4e00-\u9fffA-Za-z]{2,15})\s*(?:是指|指的是|定义为|称为|简称)\s*(.{5,80}?)(?:[。；\n]|$)",
        r"([\u4e00-\u9fffA-Za-z]{2,15})\s*(?:，|,)\s*(?:即|也就是|亦称)\s*(.{5,60}?)(?:[。；\n]|$)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            results.append((m.group(1).strip(), m.group(2).strip()))
    return results


def extract_glossary(
    skills: list[ValidatedSkill],
    *,
    min_frequency: int = 1,
) -> list[GlossaryEntry]:
    """
    从技能集合中提取术语表。

    从每个 Skill 的 body 和 raw_text 中提取定义式文本，
    去重合并后返回按频率排序的术语列表。
    """
    term_map: dict[str, GlossaryEntry] = {}

    for skill in skills:
        texts = [skill.body, skill.raw_text]
        for text in texts:
            if not text:
                continue
            entries = _extract_from_parenthetical(text) + _extract_from_definition(text)
            for term, definition in entries:
                key = term.lower().strip()
                if key in term_map:
                    term_map[key].frequency += 1
                    if skill.name not in term_map[key].source_skills:
                        term_map[key].source_skills.append(skill.name)
                    # 保留更长的定义
                    if len(definition) > len(term_map[key].definition):
                        term_map[key].definition = definition
                else:
                    term_map[key] = GlossaryEntry(
                        term=term,
                        definition=definition,
                        source_skills=[skill.name],
                        frequency=1,
                    )

    # 过滤低频 + 排序
    result = [
        e for e in term_map.values() if e.frequency >= min_frequency
    ]
    result.sort(key=lambda e: (-e.frequency, e.term))
    return result


def generate_glossary_md(
    entries: list[GlossaryEntry],
    book_name: str,
) -> str:
    """生成 glossary.md 术语表文档"""
    lines = [
        f"# {book_name} — 术语表",
        "",
        f"> 共 {len(entries)} 个术语",
        "",
        "| 术语 | 定义 | 出现频率 | 来源技能 |",
        "|------|------|---------|---------|",
    ]
    for e in entries:
        sources = ", ".join(e.source_skills[:3])
        if len(e.source_skills) > 3:
            sources += f" 等{len(e.source_skills)}个"
        lines.append(f"| {e.term} | {e.definition} | {e.frequency} | {sources} |")

    return "\n".join(lines)


def save_glossary(
    skills: list[ValidatedSkill],
    book_name: str,
    *,
    output_dir: Optional[str | Path] = None,
) -> Path:
    """提取并保存术语表到 workspace 目录"""
    entries = extract_glossary(skills)
    if not entries:
        return Path()

    base_dir = Path(output_dir or config.output_dir)
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in book_name)
    workspace = base_dir / safe_name

    glossary_path = workspace / "glossary.md"
    glossary_path.parent.mkdir(parents=True, exist_ok=True)
    glossary_path.write_text(
        generate_glossary_md(entries, book_name), encoding="utf-8"
    )
    return glossary_path
