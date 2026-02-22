"""
SKU 包装器 — Phase 4: Workspace 输出

产出目录结构:
workspace/
├── mapping.md              # 路由表——按主题/类型找 SKU
├── eureka.md               # 跨领域洞察
└── skus/
    ├── factual/{sku_id}/
    │   ├── header.md       # 摘要 + 元信息
    │   └── content.md      # 完整内容
    ├── procedural/{sku_id}/
    └── relational/{sku_id}/
"""

from __future__ import annotations

import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import config
from .skill_validator import ValidatedSkill, SKUType


# ──── SKU 文件生成 ────


def _generate_header(skill: ValidatedSkill) -> str:
    """生成 header.md：摘要 + 元信息"""
    prereqs = ", ".join(skill.prerequisites) if skill.prerequisites else "无"
    return f"""---
sku_id: {skill.sku_id}
sku_type: {skill.sku_type.value}
name: {skill.name}
trigger: "{skill.trigger}"
domain: {skill.domain}
confidence: {skill.confidence}
source_ref: "{skill.source_ref}"
prerequisites: [{prereqs}]
prompt_version: "{skill.prompt_version}"
---

# {skill.name}

> **触发条件**: {skill.trigger}
> **领域**: {skill.domain} | **类型**: {skill.sku_type.value} | **置信度**: {skill.confidence:.0%}
"""


def _generate_content(skill: ValidatedSkill) -> str:
    """生成 content.md：完整知识内容"""
    return f"""# {skill.name}

{skill.body}
"""


# ──── 路由表生成 ────


def _generate_mapping(
    skills: list[ValidatedSkill],
    book_name: str,
) -> str:
    """生成 mapping.md 多维路由表"""
    # 按类型分组
    by_type: dict[str, list[ValidatedSkill]] = defaultdict(list)
    for s in skills:
        by_type[s.sku_type.value].append(s)

    # 按领域分组
    by_domain: dict[str, list[ValidatedSkill]] = defaultdict(list)
    for s in skills:
        by_domain[s.domain].append(s)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# {book_name} — 知识库路由表",
        "",
        f"> 生成时间：{now}",
        f"> SKU 总数：{len(skills)}",
        f"> 事实型：{len(by_type.get('factual', []))} | "
        f"程序型：{len(by_type.get('procedural', []))} | "
        f"关系型：{len(by_type.get('relational', []))}",
        "",
        "---",
        "",
    ]

    # 按类型索引
    type_labels = {
        "factual": "📋 事实型知识",
        "procedural": "⚙️ 程序型知识",
        "relational": "🔗 关系型知识",
    }
    for type_val, label in type_labels.items():
        type_skills = by_type.get(type_val, [])
        if not type_skills:
            continue
        lines.append(f"## {label} ({len(type_skills)})")
        lines.append("")
        lines.append("| SKU | 触发条件 | 领域 | 置信度 |")
        lines.append("|-----|---------|------|--------|")
        for s in sorted(type_skills, key=lambda x: x.domain):
            trigger = s.trigger[:40] + "…" if len(s.trigger) > 40 else s.trigger
            link = f"[{s.name}](./skus/{type_val}/{s.sku_id}/header.md)"
            lines.append(f"| {link} | {trigger} | {s.domain} | {s.confidence:.0%} |")
        lines.append("")

    # 按领域索引
    lines.append("---")
    lines.append("")
    lines.append("## 🏷️ 按领域索引")
    lines.append("")
    for domain in sorted(by_domain.keys()):
        domain_skills = by_domain[domain]
        lines.append(f"### {domain} ({len(domain_skills)})")
        for s in domain_skills:
            lines.append(f"- [{s.name}](./skus/{s.sku_type.value}/{s.sku_id}/header.md) `{s.sku_type.value}`")
        lines.append("")

    return "\n".join(lines)


# ──── 主入口 ────


def package_skills(
    skills: list[ValidatedSkill],
    book_name: str,
    *,
    output_dir: Optional[str | Path] = None,
    create_zip: bool = True,
    eureka_content: str = "",
) -> Path:
    """
    将 SKU 列表写入 workspace 目录结构。

    Args:
        skills: 最终的 ValidatedSkill 列表（已标注 sku_type）
        book_name: 书名
        output_dir: 输出目录（默认使用配置）
        create_zip: 是否打包 ZIP
        eureka_content: 跨域洞察内容（可选）

    Returns:
        workspace 目录路径
    """
    base_dir = Path(output_dir or config.output_dir)
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in book_name)
    workspace = base_dir / safe_name
    skus_dir = workspace / "skus"

    # 创建目录结构
    for sku_type in SKUType:
        (skus_dir / sku_type.value).mkdir(parents=True, exist_ok=True)

    # 写入每个 SKU
    for skill in skills:
        sku_dir = skus_dir / skill.sku_type.value / skill.sku_id
        sku_dir.mkdir(parents=True, exist_ok=True)
        (sku_dir / "header.md").write_text(
            _generate_header(skill), encoding="utf-8"
        )
        (sku_dir / "content.md").write_text(
            _generate_content(skill), encoding="utf-8"
        )

    # 写入 mapping.md
    (workspace / "mapping.md").write_text(
        _generate_mapping(skills, book_name), encoding="utf-8"
    )

    # 写入 eureka.md
    if eureka_content:
        (workspace / "eureka.md").write_text(eureka_content, encoding="utf-8")
    else:
        (workspace / "eureka.md").write_text(
            f"# {book_name} — 跨领域洞察\n\n> 暂无洞察内容。使用全量执行后将自动生成。\n",
            encoding="utf-8",
        )

    # 打包 ZIP
    if create_zip:
        zip_path = base_dir / f"{safe_name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in workspace.rglob("*.md"):
                arcname = file.relative_to(workspace)
                zf.write(file, arcname)

    return workspace
