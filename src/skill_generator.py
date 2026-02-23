"""
Claude Skills 生成器 — Phase 5: ValidatedSkill → Claude Code Skills 标准格式

产出目录结构:
generated_skills/
├── index.md                  # 技能导航索引
├── manifest.json             # 能力摘要（机器可读）
└── {skill-slug}/
    ├── SKILL.md              # YAML frontmatter + 操作手册
    └── references/
        └── source.md         # 原始参考资料
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import config
from .skill_validator import ValidatedSkill, SKUType


# ──── 工具函数 ────


def _to_kebab(name: str) -> str:
    """将技能名转为 kebab-case slug"""
    s = re.sub(r"[^\w\s\u4e00-\u9fff-]", "", name)
    s = re.sub(r"[\s_]+", "-", s).strip("-").lower()
    return s[:60] or "unnamed-skill"


def _type_label(sku_type: SKUType) -> str:
    return {"factual": "事实查询", "procedural": "操作指南", "relational": "关系推理"}.get(
        sku_type.value, "通用"
    )


# ──── SKILL.md 生成 ────


def _generate_skill_md(skill: ValidatedSkill) -> str:
    """
    生成符合 Anthropic Claude Code Skills 规范的 SKILL.md。

    格式:
    ---
    name: kebab-case-name
    description: 一句话描述何时使用此技能
    ---
    # 标题
    ## When to Use
    ## Core Logic
    ## References
    """
    prereqs = ", ".join(skill.prerequisites) if skill.prerequisites else "无"
    confidence_pct = f"{skill.confidence:.0%}"

    # 构建描述：触发条件就是最佳的 description
    description = skill.trigger.rstrip("。.") if skill.trigger else skill.name

    frontmatter = f"""---
name: {_to_kebab(skill.name)}
description: {description}
---"""

    body_section = skill.body.strip() if skill.body else "（内容待补充）"

    return f"""{frontmatter}

# {skill.name}

## When to Use

{skill.trigger}

## Core Logic

{body_section}

## Metadata

| 属性 | 值 |
|------|-----|
| 领域 | {skill.domain} |
| 类型 | {_type_label(skill.sku_type)} ({skill.sku_type.value}) |
| 置信度 | {confidence_pct} |
| 前置条件 | {prereqs} |
| 来源 | {skill.source_ref} |
"""


def _generate_reference_md(skill: ValidatedSkill) -> str:
    """生成 references/source.md：原始提取文本"""
    return f"""# {skill.name} — 参考资料

> 来源 chunk #{skill.source_chunk_index}
> 上下文: {skill.source_context}

---

{skill.raw_text}
"""


# ──── 索引生成 ────


def _generate_index(
    skills: list[ValidatedSkill],
    book_name: str,
) -> str:
    """生成 index.md 技能导航索引"""
    by_domain: dict[str, list[ValidatedSkill]] = defaultdict(list)
    for s in skills:
        by_domain[s.domain].append(s)

    by_type: dict[str, int] = defaultdict(int)
    for s in skills:
        by_type[s.sku_type.value] += 1

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# {book_name} — Claude Skills 索引",
        "",
        f"> 生成时间：{now}",
        f"> 技能总数：{len(skills)}",
        f"> 事实型：{by_type.get('factual', 0)} | "
        f"程序型：{by_type.get('procedural', 0)} | "
        f"关系型：{by_type.get('relational', 0)}",
        "",
        "---",
        "",
    ]

    for domain in sorted(by_domain.keys()):
        domain_skills = by_domain[domain]
        lines.append(f"## {domain} ({len(domain_skills)})")
        lines.append("")
        lines.append("| 技能 | 触发条件 | 类型 | 置信度 |")
        lines.append("|------|---------|------|--------|")
        for s in sorted(domain_skills, key=lambda x: x.name):
            slug = _to_kebab(s.name)
            trigger = (
                s.trigger[:50] + "…" if len(s.trigger) > 50 else s.trigger
            )
            lines.append(
                f"| [{s.name}](./{slug}/SKILL.md) "
                f"| {trigger} "
                f"| {s.sku_type.value} "
                f"| {s.confidence:.0%} |"
            )
        lines.append("")

    return "\n".join(lines)


def _generate_manifest(
    skills: list[ValidatedSkill],
    book_name: str,
) -> dict:
    """生成 manifest.json 能力摘要"""
    return {
        "name": book_name,
        "generated_at": datetime.now().isoformat(),
        "total_skills": len(skills),
        "domains": list({s.domain for s in skills}),
        "type_distribution": {
            t.value: sum(1 for s in skills if s.sku_type == t)
            for t in SKUType
        },
        "skills": [
            {
                "slug": _to_kebab(s.name),
                "name": s.name,
                "domain": s.domain,
                "type": s.sku_type.value,
                "trigger": s.trigger,
                "confidence": s.confidence,
            }
            for s in skills
        ],
    }


# ──── 主入口 ────


def generate_claude_skills(
    skills: list[ValidatedSkill],
    book_name: str,
    *,
    output_dir: Optional[str | Path] = None,
) -> Path:
    """
    将 ValidatedSkill 列表转为 Claude Code Skills 标准目录结构。

    生成后自动扫描注册到 SkillRegistry（热插拔）。

    Args:
        skills: 最终的 ValidatedSkill 列表
        book_name: 书名/文档名
        output_dir: 输出根目录（默认使用配置）

    Returns:
        生成的 skills 目录路径
    """
    base_dir = Path(output_dir or config.output_dir)
    safe_name = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in book_name
    )
    skills_dir = base_dir / safe_name / "claude_skills"

    # 去重：同名技能只保留置信度最高的
    seen: dict[str, ValidatedSkill] = {}
    for s in skills:
        slug = _to_kebab(s.name)
        if slug not in seen or s.confidence > seen[slug].confidence:
            seen[slug] = s
    deduped = list(seen.values())

    # 生成每个技能的 SKILL.md + scripts/ 模板
    for skill in deduped:
        slug = _to_kebab(skill.name)
        skill_dir = skills_dir / slug
        skill_dir.mkdir(parents=True, exist_ok=True)

        (skill_dir / "SKILL.md").write_text(
            _generate_skill_md(skill), encoding="utf-8"
        )

        ref_dir = skill_dir / "references"
        ref_dir.mkdir(exist_ok=True)
        (ref_dir / "source.md").write_text(
            _generate_reference_md(skill), encoding="utf-8"
        )

        # 生成 scripts/ 模板目录
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        run_py = scripts_dir / "run.py"
        if not run_py.exists():
            run_py.write_text(
                f'"""Skill 执行脚本模板 — {skill.name}"""\n\n'
                f'# 触发条件: {skill.trigger}\n'
                f'# 领域: {skill.domain}\n\n'
                f'def main():\n'
                f'    """实现 Skill 逻辑"""\n'
                f'    pass\n\n'
                f'if __name__ == "__main__":\n'
                f'    main()\n',
                encoding="utf-8",
            )

    # 生成索引
    (skills_dir / "index.md").write_text(
        _generate_index(deduped, book_name), encoding="utf-8"
    )

    # 生成 manifest.json
    (skills_dir / "manifest.json").write_text(
        json.dumps(
            _generate_manifest(deduped, book_name),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # 热插拔注册：扫描新生成的 Skills 目录
    try:
        from .skill_registry import SkillRegistry
        registry = SkillRegistry()
        registered = registry.scan(skills_dir)
        import logging
        logging.getLogger(__name__).info(f"🔌 自动注册 {registered} 个 Skill")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Skill 自动注册失败: {e}")

    return skills_dir

