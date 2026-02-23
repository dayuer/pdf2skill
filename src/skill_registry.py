"""
Skill 热插拔注册表 — 基于文件系统自动发现和注册 Skills。

扫描目录下所有包含 SKILL.md 的子目录，解析 YAML frontmatter，
构建内存注册表支持按触发条件和领域检索。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SkillEntry:
    """单个 Skill 的注册信息"""

    slug: str
    name: str
    description: str
    domain: str = ""
    skill_type: str = ""
    trigger: str = ""
    skill_dir: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "domain": self.domain,
            "type": self.skill_type,
            "trigger": self.trigger,
            "skill_dir": self.skill_dir,
            "confidence": self.confidence,
        }


def _parse_yaml_frontmatter(text: str) -> dict[str, str]:
    """解析 SKILL.md 的 YAML frontmatter（--- 包裹的头部）"""
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return {}
    result: dict[str, str] = {}
    for line in match.group(1).split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip()] = val.strip().strip("\"'")
    return result


def _extract_section(text: str, heading: str) -> str:
    """提取 Markdown 中指定 ## 标题下的文本内容"""
    pattern = rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


class SkillRegistry:
    """
    Skill 注册表 — 文件系统即注册表。

    扫描指定目录下所有含 SKILL.md 的子目录，自动解析并注册。
    支持按触发条件模糊匹配和按领域检索。
    """

    def __init__(self) -> None:
        self._skills: dict[str, SkillEntry] = {}

    @property
    def count(self) -> int:
        return len(self._skills)

    def scan(self, skills_dir: str | Path) -> int:
        """
        扫描目录，自动注册所有包含 SKILL.md 的子目录。

        Returns:
            新注册的 Skill 数量
        """
        skills_dir = Path(skills_dir)
        if not skills_dir.exists():
            logger.warning(f"Skill 目录不存在: {skills_dir}")
            return 0

        registered = 0
        for skill_md in skills_dir.rglob("SKILL.md"):
            try:
                entry = self._parse_skill_dir(skill_md.parent)
                if entry:
                    self.register(entry)
                    registered += 1
            except Exception as e:
                logger.error(f"解析 Skill 失败 [{skill_md}]: {e}")

        logger.info(f"✅ 扫描完成：注册 {registered} 个 Skill（总计 {self.count}）")
        return registered

    def register(self, entry: SkillEntry) -> None:
        """注册单个 Skill（覆盖同名）"""
        self._skills[entry.slug] = entry

    def unregister(self, slug: str) -> bool:
        """取消注册"""
        return self._skills.pop(slug, None) is not None

    def get(self, slug: str) -> Optional[SkillEntry]:
        """按 slug 获取 Skill"""
        return self._skills.get(slug)

    def find_by_trigger(self, query: str) -> list[SkillEntry]:
        """按触发条件模糊匹配"""
        query_lower = query.lower()
        return [
            s
            for s in self._skills.values()
            if query_lower in s.trigger.lower() or query_lower in s.description.lower()
        ]

    def find_by_domain(self, domain: str) -> list[SkillEntry]:
        """按领域检索（支持前缀匹配，如 '保险' 可匹配 '保险·理赔'）"""
        return [
            s
            for s in self._skills.values()
            if s.domain.startswith(domain)
        ]

    def list_all(self) -> list[SkillEntry]:
        """返回所有已注册 Skill"""
        return list(self._skills.values())

    def to_manifest(self) -> dict:
        """导出 manifest.json 格式"""
        return {
            "total_skills": self.count,
            "domains": sorted({s.domain for s in self._skills.values() if s.domain}),
            "skills": [s.to_dict() for s in self._skills.values()],
        }

    def _parse_skill_dir(self, skill_dir: Path) -> Optional[SkillEntry]:
        """解析 Skill 目录，从 SKILL.md 提取元信息"""
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return None

        text = skill_md.read_text(encoding="utf-8")
        fm = _parse_yaml_frontmatter(text)

        slug = fm.get("name", skill_dir.name)
        description = fm.get("description", "")
        trigger = _extract_section(text, "When to Use") or description

        # 提取 Metadata 表格中的领域和类型
        domain, skill_type, confidence = "", "", 0.0
        meta_section = _extract_section(text, "Metadata")
        if meta_section:
            for line in meta_section.split("\n"):
                if "领域" in line:
                    parts = line.split("|")
                    if len(parts) >= 3:
                        domain = parts[2].strip()
                elif "类型" in line:
                    parts = line.split("|")
                    if len(parts) >= 3:
                        skill_type = parts[2].strip()
                elif "置信度" in line:
                    parts = line.split("|")
                    if len(parts) >= 3:
                        val = parts[2].strip().rstrip("%")
                        try:
                            confidence = float(val) / 100 if "%" not in parts[2] else float(val) / 100
                        except ValueError:
                            pass

        return SkillEntry(
            slug=slug,
            name=fm.get("name", skill_dir.name),
            description=description,
            domain=domain,
            skill_type=skill_type,
            trigger=trigger,
            skill_dir=str(skill_dir),
            confidence=confidence,
        )
