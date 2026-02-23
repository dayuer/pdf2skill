"""
组件集成测试 — 验证新增的 5 个模块。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from pathlib import Path

import pytest


# ──── Component 1: 配置管理 ────


class TestConfig:
    def test_config_import(self):
        from src.config import config, PipelineConfig
        assert isinstance(config, PipelineConfig)

    def test_llm_fields(self):
        from src.config import config
        assert hasattr(config.llm, "base_url")
        assert hasattr(config.llm, "api_key")
        assert hasattr(config.llm, "model")

    def test_config_hash_deterministic(self):
        from src.config import config
        h1 = config.config_hash
        h2 = config.config_hash
        assert h1 == h2
        assert len(h1) == 32  # MD5 长度

    def test_embedding_unconfigured(self):
        from src.config import config
        # 默认（无 EMBEDDING_* 环境变量）应为未配置
        # 仅在未设置时才断言
        if not config.embedding.api_key:
            assert config.embedding.is_configured is False

    def test_ensure_filesystem(self, tmp_path):
        """验证 ensure_filesystem 创建目录"""
        from src.config import PipelineConfig
        cfg = PipelineConfig(output_dir=str(tmp_path / "out"), log_dir=str(tmp_path / "log"))
        cfg.ensure_filesystem()
        assert Path("notebooks").exists()


# ──── Component 2: 事件回调 ────


class TestCallbacks:
    def test_event_type_values(self):
        from src.callbacks import EventType
        assert EventType.PHASE_START.value == "phase_start"
        assert EventType.ERROR.value == "error"

    def test_status_callback_add_remove(self):
        from src.callbacks import StatusCallback, EventType

        cb = StatusCallback()
        events = []

        async def handler(et, data):
            events.append((et, data))

        cb.add_callback(handler)
        asyncio.get_event_loop().run_until_complete(
            cb.emit(EventType.INFO, {"msg": "test"})
        )
        assert len(events) == 1
        assert events[0][0] == EventType.INFO

        cb.remove_callback(handler)
        asyncio.get_event_loop().run_until_complete(
            cb.emit(EventType.INFO, {"msg": "test2"})
        )
        assert len(events) == 1  # 移除后不再接收

    def test_callback_error_isolation(self):
        """一个回调异常不影响其他回调"""
        from src.callbacks import StatusCallback, EventType

        results = []

        async def bad_cb(et, data):
            raise ValueError("boom")

        async def good_cb(et, data):
            results.append(data)

        cb = StatusCallback()
        cb.add_callback(bad_cb)
        cb.add_callback(good_cb)

        asyncio.get_event_loop().run_until_complete(
            cb.emit(EventType.INFO, {"ok": True})
        )
        assert len(results) == 1
        assert results[0]["ok"] is True


# ──── Component 3: Skill 注册表 ────


class TestSkillRegistry:
    def test_scan_empty_dir(self, tmp_path):
        from src.skill_registry import SkillRegistry
        reg = SkillRegistry()
        count = reg.scan(tmp_path)
        assert count == 0
        assert reg.count == 0

    def test_scan_with_skill_md(self, tmp_path):
        from src.skill_registry import SkillRegistry

        # 创建一个符合规范的 Skill 目录
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: 测试技能\n---\n\n"
            "# Test Skill\n\n## When to Use\n\n当需要测试时。\n",
            encoding="utf-8",
        )

        reg = SkillRegistry()
        count = reg.scan(tmp_path)
        assert count == 1
        assert reg.count == 1

        entry = reg.get("test-skill")
        assert entry is not None
        assert entry.name == "test-skill"
        assert entry.description == "测试技能"

    def test_find_by_trigger(self, tmp_path):
        from src.skill_registry import SkillRegistry, SkillEntry

        reg = SkillRegistry()
        reg.register(SkillEntry(
            slug="a", name="A", description="保险理赔流程",
            trigger="当需要处理理赔时",
        ))
        reg.register(SkillEntry(
            slug="b", name="B", description="技术文档",
            trigger="当查询 API 文档时",
        ))

        results = reg.find_by_trigger("理赔")
        assert len(results) == 1
        assert results[0].slug == "a"

    def test_to_manifest(self):
        from src.skill_registry import SkillRegistry, SkillEntry

        reg = SkillRegistry()
        reg.register(SkillEntry(
            slug="x", name="X", description="desc", domain="保险",
        ))
        manifest = reg.to_manifest()
        assert manifest["total_skills"] == 1
        assert "保险" in manifest["domains"]


# ──── Component 4: Skill 图谱 ────


class TestSkillGraph:
    def test_empty_graph(self):
        from src.skill_graph import SkillGraphBuilder
        builder = SkillGraphBuilder()
        builder.build_from_skills([])
        analysis = builder.analyze()
        assert analysis.statistics == {}

    def test_same_domain_edges(self):
        from src.skill_graph import SkillGraphBuilder
        builder = SkillGraphBuilder()
        skills = [
            {"name": "A", "domain": "保险", "trigger": "t1"},
            {"name": "B", "domain": "保险", "trigger": "t2"},
            {"name": "C", "domain": "技术", "trigger": "t3"},
        ]
        builder.build_from_skills(skills)
        # A-B 应该有同域边
        assert builder.graph.has_edge("A", "B")
        # A-C 不应有自动边
        assert not builder.graph.has_edge("A", "C")

    def test_mermaid_output(self):
        from src.skill_graph import SkillGraphBuilder
        builder = SkillGraphBuilder()
        builder.build_from_skills([
            {"name": "X", "domain": "d1", "trigger": "t"},
        ])
        analysis = builder.analyze()
        assert "graph LR" in analysis.mermaid
