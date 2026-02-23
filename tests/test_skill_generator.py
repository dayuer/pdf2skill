"""
skill_generator 单测 — 验证 Claude Skills 输出格式
"""

import json
import tempfile
from pathlib import Path

from src.skill_validator import ValidatedSkill, SKUType, ValidationStatus
from src.skill_generator import generate_claude_skills, _to_kebab


def _make_skill(name="测试技能", domain="保险", sku_type=SKUType.PROCEDURAL, **kw):
    defaults = dict(
        trigger="当用户询问保险条款时",
        prerequisites=["基础保险知识"],
        source_ref="test.pdf:p12",
        confidence=0.85,
        body="## 步骤\n1. 查阅条款\n2. 解释含义",
        raw_text="原始文本内容...",
        status=ValidationStatus.PASS,
        source_chunk_index=0,
        source_context="第一章",
    )
    defaults.update(kw)
    return ValidatedSkill(name=name, domain=domain, sku_type=sku_type, **defaults)


class TestToKebab:
    def test_chinese(self):
        assert _to_kebab("保险理赔流程") == "保险理赔流程"

    def test_english(self):
        assert _to_kebab("Insurance Claim Process") == "insurance-claim-process"

    def test_mixed(self):
        result = _to_kebab("API 调用指南 v2")
        assert " " not in result
        assert result.islower() or any("\u4e00" <= c <= "\u9fff" for c in result)

    def test_empty(self):
        assert _to_kebab("") == "unnamed-skill"


class TestGenerateClaudeSkills:
    def test_basic_output_structure(self):
        skills = [_make_skill("保险理赔"), _make_skill("免责条款", domain="法律")]
        with tempfile.TemporaryDirectory() as td:
            out = generate_claude_skills(skills, "测试文档", output_dir=td)
            assert out.exists()
            assert (out / "index.md").exists()
            assert (out / "manifest.json").exists()

    def test_skill_md_format(self):
        skills = [_make_skill("保险理赔流程")]
        with tempfile.TemporaryDirectory() as td:
            out = generate_claude_skills(skills, "测试", output_dir=td)
            # 找到 SKILL.md
            skill_mds = list(out.rglob("SKILL.md"))
            assert len(skill_mds) == 1
            content = skill_mds[0].read_text(encoding="utf-8")
            # YAML frontmatter
            assert content.startswith("---")
            assert "name:" in content
            assert "description:" in content
            # 必要章节
            assert "## When to Use" in content
            assert "## Core Logic" in content

    def test_references_dir(self):
        skills = [_make_skill()]
        with tempfile.TemporaryDirectory() as td:
            out = generate_claude_skills(skills, "测试", output_dir=td)
            refs = list(out.rglob("references/source.md"))
            assert len(refs) == 1

    def test_manifest_json(self):
        skills = [
            _make_skill("A", sku_type=SKUType.FACTUAL),
            _make_skill("B", sku_type=SKUType.PROCEDURAL),
        ]
        with tempfile.TemporaryDirectory() as td:
            out = generate_claude_skills(skills, "测试", output_dir=td)
            manifest = json.loads((out / "manifest.json").read_text())
            assert manifest["total_skills"] == 2
            assert "domains" in manifest
            assert "type_distribution" in manifest

    def test_dedup_by_name(self):
        """同名技能只保留置信度最高的"""
        skills = [
            _make_skill("重复技能", confidence=0.6),
            _make_skill("重复技能", confidence=0.9),
        ]
        with tempfile.TemporaryDirectory() as td:
            out = generate_claude_skills(skills, "测试", output_dir=td)
            skill_mds = list(out.rglob("SKILL.md"))
            assert len(skill_mds) == 1
            content = skill_mds[0].read_text()
            assert "90%" in content  # 保留高置信度的

    def test_index_links(self):
        skills = [_make_skill("保险理赔"), _make_skill("免责条款")]
        with tempfile.TemporaryDirectory() as td:
            out = generate_claude_skills(skills, "测试", output_dir=td)
            index = (out / "index.md").read_text()
            assert "SKILL.md" in index
            assert "保险理赔" in index
            assert "免责条款" in index
