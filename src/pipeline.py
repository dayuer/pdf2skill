"""
Pipeline 主流程编排器 — 串联 Phase 0 → Phase 4

执行流：
  文档加载 → 清洗切分 → 语义粗筛 → Schema 生成 → 并行提取 → 校验 → 去重合并 → 打包输出
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import PipelineConfig, config
from .document_loader import LoadResult, load_document
from .llm_client import AsyncDeepSeekClient, DeepSeekClient
from .markdown_chunker import ChunkResult, chunk_markdown
from .semantic_filter import filter_chunks
from .schema_generator import SkillSchema, generate_schema
from .sku_classifier import classify_batch
from .skill_extractor import extract_skills_batch
from .skill_generator import generate_claude_skills
from .glossary_extractor import save_glossary
from .skill_packager import package_skills
from .skill_reducer import cluster_skills, reduce_all_clusters
from .skill_validator import SkillValidator, SKUType, ValidatedSkill


@dataclass
class PipelineResult:
    """Pipeline 执行结果"""

    # 输出目录路径
    output_dir: str
    # 统计信息
    total_chunks: int = 0
    filtered_chunks: int = 0
    raw_skills_count: int = 0
    valid_skills_count: int = 0
    failed_skills_count: int = 0
    clusters_count: int = 0
    final_skills_count: int = 0
    # Claude Skills 输出路径
    claude_skills_dir: str = ""
    # 时间统计（秒）
    elapsed_seconds: float = 0
    # 阶段耗时
    phase_timings: dict[str, float] = field(default_factory=dict)
    # 文档信息
    doc_name: str = ""
    doc_format: str = ""
    chunk_strategy: str = ""
    # Schema 信息
    book_type: str = ""
    domains: list[str] = field(default_factory=list)
    # SKU 分类统计
    sku_stats: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        """生成执行摘要"""
        lines = [
            f"📄 文档：{self.doc_name}（{self.doc_format}）",
            f"📊 切分策略：{self.chunk_strategy}，共 {self.total_chunks} 块（筛后 {self.filtered_chunks} 块）",
            f"🔍 提取：{self.raw_skills_count} 个 Raw Skill → {self.valid_skills_count} 个通过校验",
            f"❌ 校验失败：{self.failed_skills_count} 个",
            f"🔗 去重聚类：{self.clusters_count} 簇 → {self.final_skills_count} 个 Final Skill",
            f"📊 SKU 分布：{self.sku_stats}",
            f"📦 输出目录：{self.output_dir}",
            f"⏱️ 总耗时：{self.elapsed_seconds:.1f}s",
        ]
        if self.phase_timings:
            lines.append("  阶段耗时：")
            for phase, t in self.phase_timings.items():
                lines.append(f"    {phase}: {t:.1f}s")
        return "\n".join(lines)


def run_pipeline(
    filepath: str | Path,
    *,
    book_name: Optional[str] = None,
    schema_override: Optional[str | Path] = None,
    output_dir: Optional[str | Path] = None,
    max_chunks: Optional[int] = None,
    cfg: Optional[PipelineConfig] = None,
) -> PipelineResult:
    """
    同步执行完整 Pipeline。

    Args:
        filepath: 文档路径（PDF/TXT/EPUB）
        book_name: 书名（默认使用文件名）
        schema_override: 可选的 Schema JSON 文件路径（人工 Override）
        output_dir: 输出目录
        max_chunks: 最大处理块数（超大文档采样模式），None 表示全量
        cfg: Pipeline 配置（默认使用全局配置）

    Returns:
        PipelineResult 执行结果
    """
    return asyncio.run(
        run_pipeline_async(
            filepath,
            book_name=book_name,
            schema_override=schema_override,
            output_dir=output_dir,
            max_chunks=max_chunks,
            cfg=cfg,
        )
    )


async def run_pipeline_async(
    filepath: str | Path,
    *,
    book_name: Optional[str] = None,
    schema_override: Optional[str | Path] = None,
    output_dir: Optional[str | Path] = None,
    max_chunks: Optional[int] = None,
    cfg: Optional[PipelineConfig] = None,
) -> PipelineResult:
    """
    异步执行完整 Pipeline。

    数据流：
    1. Phase 0: 文档加载 + Schema 生成
    2. Phase 1: 清洗 + AST 切分
    3. Phase 2: 并行 Skill 提取 + 校验
    4. Phase 3: 向量去重 + R1 合并
    5. Phase 4: 打包输出
    """
    if cfg is None:
        cfg = config

    t_start = time.monotonic()
    timings: dict[str, float] = {}
    result = PipelineResult(output_dir="")

    # ── Phase 0A：文档加载 ──
    t0 = time.monotonic()
    print(f"📄 加载文档：{filepath}")
    load_result = load_document(filepath)
    doc_name = book_name or load_result.doc_name
    result.doc_name = doc_name
    result.doc_format = load_result.format.value

    if load_result.warnings:
        for w in load_result.warnings:
            print(f"  ⚠️ {w}")

    timings["文档加载"] = time.monotonic() - t0

    # ── Phase 1：清洗 + AST 切分 ──
    t0 = time.monotonic()
    print(f"✂️ 切分文本...")
    chunk_result = chunk_markdown(
        load_result.markdown,
        doc_name,
        split_level=cfg.chunk.split_level,
        max_chars=cfg.chunk.max_chunk_chars,
        min_chars=cfg.chunk.min_chunk_chars,
    )
    result.total_chunks = len(chunk_result.chunks)
    result.chunk_strategy = chunk_result.strategy
    print(f"  策略：{chunk_result.strategy}，{result.total_chunks} 个文本块")
    timings["切分清洗"] = time.monotonic() - t0

    if not chunk_result.chunks:
        print("❌ 切分结果为空，无法继续")
        result.elapsed_seconds = time.monotonic() - t_start
        result.phase_timings = timings
        return result

    # ── Phase 1C：语义密度粗筛 ──
    t0 = time.monotonic()
    print(f"🧹 语义密度粗筛...")
    filter_result = filter_chunks(chunk_result.chunks)
    result.filtered_chunks = filter_result.kept_count
    print(f"  保留：{filter_result.kept_count}，丢弃：{filter_result.dropped_count}")
    if filter_result.dropped:
        for d in filter_result.dropped[:3]:
            preview = d.chunk.content[:40].replace('\n', ' ')
            print(f"    🗑️ {d.reason}：{preview}...")
    chunks_to_process = filter_result.kept
    timings["语义粗筛"] = time.monotonic() - t0

    # ── 大文档采样 ──
    if max_chunks and len(chunks_to_process) > max_chunks:
        # 均匀采样，保持文档覆盖
        step = len(chunks_to_process) / max_chunks
        sampled = [chunks_to_process[int(i * step)] for i in range(max_chunks)]
        print(f"📐 大文档采样：{len(chunks_to_process)} → {len(sampled)} 块（均匀采样）")
        chunks_to_process = sampled

    # ── Phase 0B：Schema 生成 ──
    t0 = time.monotonic()
    if schema_override:
        print(f"📋 使用人工 Schema：{schema_override}")
        schema = SkillSchema.load(schema_override)
    else:
        print(f"🧠 R1 推断 Schema...")
        sync_client = DeepSeekClient()
        schema = generate_schema(load_result.markdown, doc_name, client=sync_client)
        print(f"  书籍类型：{schema.book_type}")
        print(f"  领域：{schema.domains}")

    result.book_type = schema.book_type
    result.domains = schema.domains
    timings["Schema生成"] = time.monotonic() - t0

    # ── Phase 2：并行 Skill 提取 ──
    t0 = time.monotonic()
    print(f"⛏️ 并行提取 Skill（并发数：{cfg.max_concurrent_requests}）...")
    async_client = AsyncDeepSeekClient()
    raw_skills = await extract_skills_batch(
        chunks_to_process,
        schema,
        client=async_client,
    )
    result.raw_skills_count = len(raw_skills)
    print(f"  提取到 {len(raw_skills)} 个 Raw Skill")
    timings["Skill提取"] = time.monotonic() - t0

    if not raw_skills:
        print("⚠️ 未提取到任何 Skill")
        result.elapsed_seconds = time.monotonic() - t_start
        result.phase_timings = timings
        return result

    # ── Phase 2B：校验 ──
    t0 = time.monotonic()
    print(f"🔍 校验 Skill...")
    validator = SkillValidator()
    source_texts = [c.content for c in chunks_to_process]

    # 为每个 raw_skill 找到对应的 source_text
    raw_source_texts = []
    for rs in raw_skills:
        if rs.source_chunk_index < len(source_texts):
            raw_source_texts.append(source_texts[rs.source_chunk_index])
        else:
            raw_source_texts.append(None)

    passed, failed = validator.validate_batch(raw_skills, source_texts=raw_source_texts)
    result.valid_skills_count = len(passed)
    result.failed_skills_count = len(failed)
    print(f"  ✅ 通过：{len(passed)}，❌ 失败：{len(failed)}")

    if failed:
        for f in failed[:3]:
            print(f"    ❌ {f.name or '(unnamed)'}: {f.warnings}")

    timings["校验"] = time.monotonic() - t0

    if not passed:
        print("⚠️ 无 Skill 通过校验")
        result.elapsed_seconds = time.monotonic() - t_start
        result.phase_timings = timings
        return result

    # ── Phase 3：去重 + R1 合并 ──
    t0 = time.monotonic()
    print(f"🔗 向量去重聚类...")
    clusters = cluster_skills(passed, threshold=cfg.dedup_similarity_threshold)
    result.clusters_count = len(clusters)
    multi_clusters = [c for c in clusters if len(c.skills) > 1]
    print(f"  {len(clusters)} 簇（其中 {len(multi_clusters)} 簇需要合并）")

    if multi_clusters:
        print(f"  🧠 R1 合并同类项...")
        final_skills = await reduce_all_clusters(clusters, client=async_client)
    else:
        final_skills = [c.skills[0] for c in clusters]

    result.final_skills_count = len(final_skills)
    timings["去重合并"] = time.monotonic() - t0

    # ── Phase 3.5：SKU 分类 ──
    t0 = time.monotonic()
    print(f"🏷️ SKU 分类...")
    final_skills = classify_batch(final_skills)
    sku_stats = {}
    for s in final_skills:
        sku_stats[s.sku_type.value] = sku_stats.get(s.sku_type.value, 0) + 1
    result.sku_stats = sku_stats
    print(f"  📋 factual: {sku_stats.get('factual', 0)} | ⚙️ procedural: {sku_stats.get('procedural', 0)} | 🔗 relational: {sku_stats.get('relational', 0)}")
    timings["SKU分类"] = time.monotonic() - t0

    # ── Phase 4：打包输出 ──
    t0 = time.monotonic()
    print(f"📦 打包输出...")
    out_path = package_skills(
        final_skills,
        doc_name,
        output_dir=output_dir or cfg.output_dir,
    )
    result.output_dir = str(out_path)
    print(f"  输出目录：{out_path}")
    timings["打包输出"] = time.monotonic() - t0

    # ── Phase 5：Claude Skills 生成 ──
    t0 = time.monotonic()
    print(f"🎯 生成 Claude Skills...")
    skills_path = generate_claude_skills(
        final_skills,
        doc_name,
        output_dir=output_dir or cfg.output_dir,
    )
    result.claude_skills_dir = str(skills_path)
    print(f"  Claude Skills 目录：{skills_path}")
    timings["Claude Skills"] = time.monotonic() - t0

    # ── Phase 6：术语表提取 ──
    t0 = time.monotonic()
    glossary_path = save_glossary(
        final_skills, doc_name, output_dir=output_dir or cfg.output_dir
    )
    if glossary_path and glossary_path.exists():
        print(f"📚 术语表：{glossary_path}")
    timings["术语表"] = time.monotonic() - t0

    # ── 完成 ──
    result.elapsed_seconds = time.monotonic() - t_start
    result.phase_timings = timings
    print(f"\n{'=' * 50}")
    print(result.summary())

    return result


# ──── CLI 入口 ────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法：python -m src.pipeline <文档路径> [书名] [--max-chunks N]")
        print("支持格式：PDF, TXT, EPUB")
        sys.exit(1)

    filepath = sys.argv[1]
    name = None
    mc = None

    # 解析参数
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--max-chunks" and i + 1 < len(args):
            mc = int(args[i + 1])
            i += 2
        elif name is None:
            name = args[i]
            i += 1
        else:
            i += 1

    result = run_pipeline(filepath, book_name=name, max_chunks=mc)
    print(f"\n完成。最终 Skill 数量：{result.final_skills_count}")
