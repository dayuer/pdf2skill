"""
Skill 打包器 — Phase 4: Skill Packaging

输出：
1. 每个 Skill 一个 .md 文件
2. index.md 路由表（按 domain 分组）
3. ZIP 打包
"""

from __future__ import annotations

import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import config
from .skill_validator import ValidatedSkill


def _skill_to_markdown(skill: ValidatedSkill) -> str:
    """将 ValidatedSkill 转为标准 Markdown 文件内容"""
    # 构建 Frontmatter
    prereqs = "\n".join(f'  - "{p}"' for p in skill.prerequisites) if skill.prerequisites else '  - "无"'

    fm = f"""---
name: {skill.name}
trigger: "{skill.trigger}"
domain: {skill.domain}
prerequisites:
{prereqs}
source_ref: "{skill.source_ref}"
confidence: {skill.confidence}
prompt_version: "{skill.prompt_version}"
---"""

    return f"{fm}\n\n{skill.body}"


def _generate_index(
    skills: list[ValidatedSkill],
    book_name: str,
) -> str:
    """生成 index.md 路由表"""
    # 按 domain 分组
    by_domain: dict[str, list[ValidatedSkill]] = defaultdict(list)
    for s in skills:
        by_domain[s.domain].append(s)

    lines = [
        f"# {book_name} — Skill 路由表",
        "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> Skill 总数：{len(skills)}",
        f"> 领域数量：{len(by_domain)}",
        "",
    ]

    for domain in sorted(by_domain.keys()):
        domain_skills = by_domain[domain]
        lines.append(f"## {domain}")
        lines.append("")
        lines.append("| Skill | 触发条件 | 置信度 |")
        lines.append("|-------|---------|--------|")
        for s in domain_skills:
            filename = f"{s.name}.md"
            trigger_short = s.trigger[:40] + "..." if len(s.trigger) > 40 else s.trigger
            lines.append(f"| [{s.name}](./{filename}) | {trigger_short} | {s.confidence:.0%} |")
        lines.append("")

    return "\n".join(lines)


def package_skills(
    skills: list[ValidatedSkill],
    book_name: str,
    *,
    output_dir: Optional[str | Path] = None,
    create_zip: bool = True,
) -> Path:
    """
    将 Skill 列表写入文件系统。

    Args:
        skills: 最终的 ValidatedSkill 列表
        book_name: 书名（用于目录命名和路由表标题）
        output_dir: 输出目录（默认使用配置）
        create_zip: 是否打包为 ZIP

    Returns:
        输出目录路径
    """
    base_dir = Path(output_dir or config.output_dir)
    # 用书名创建子目录
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in book_name)
    skill_dir = base_dir / safe_name
    skill_dir.mkdir(parents=True, exist_ok=True)

    # 写入每个 Skill 文件
    for skill in skills:
        filename = f"{skill.name}.md"
        filepath = skill_dir / filename
        filepath.write_text(
            _skill_to_markdown(skill),
            encoding="utf-8",
        )

    # 写入 index.md
    index_path = skill_dir / "index.md"
    index_path.write_text(
        _generate_index(skills, book_name),
        encoding="utf-8",
    )

    # 打包 ZIP
    if create_zip:
        zip_path = base_dir / f"{safe_name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in skill_dir.rglob("*.md"):
                arcname = file.relative_to(skill_dir)
                zf.write(file, arcname)

    return skill_dir
