"""Skills CRUD + 注册表 / 知识图谱 / 向量检索"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Query, HTTPException

from .config import config
from .deps import NotebookDep
from .notebook_store import FileNotebook
from .skill_registry import SkillRegistry
from .skill_graph import SkillGraphBuilder
from .vector_store import SkillVectorStore

router = APIRouter(prefix="/api", tags=["skills"])

_skill_registry = SkillRegistry()
_vector_store = SkillVectorStore()


@router.get("/session/{notebook_id}/skills")
async def api_session_skills(nb: NotebookDep):
    """获取已提取的所有 Skill。"""
    return nb.load_skills()


@router.post("/session/{notebook_id}/generate-skills")
async def api_generate_skills(nb: NotebookDep):
    """生成 Claude Code Skills 标准格式。"""
    from .skill_generator import generate_claude_skills
    from .skill_validator import ValidatedSkill, SKUType

    skills_data = nb.load_skills()
    if not skills_data:
        raise HTTPException(400, "无已提取的技能，请先执行提取")

    validated = []
    for sd in skills_data:
        try:
            validated.append(ValidatedSkill(
                name=sd.get("name", ""), trigger=sd.get("trigger", ""),
                domain=sd.get("domain", "general"), prerequisites=sd.get("prerequisites", []),
                source_ref=sd.get("source_ref", ""), confidence=sd.get("confidence", 0.5),
                body=sd.get("body", ""), raw_text=sd.get("raw_text", ""),
                sku_type=SKUType(sd.get("sku_type", "procedural")),
                source_chunk_index=sd.get("source_chunk_index", 0),
                source_context=sd.get("source_context", ""),
            ))
        except Exception:
            continue

    if not validated:
        raise HTTPException(400, "无有效技能数据")

    meta = nb.load_meta() or {}
    skills_path = generate_claude_skills(validated, meta.get("doc_name", "document"))
    manifest_path = skills_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    return {"ok": True, "skills_dir": str(skills_path), "total_skills": len(validated), "manifest": manifest}


@router.get("/session/{notebook_id}/skill/{skill_slug}")
async def api_get_skill(nb: NotebookDep, skill_slug: str):
    """获取单个 Claude Skill 完整内容。"""
    meta = nb.load_meta() or {}
    doc_name = meta.get("doc_name", "document")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in doc_name)
    base = Path(config.output_dir) / safe_name / "claude_skills" / skill_slug

    skill_md_path = base / "SKILL.md"
    if not skill_md_path.exists():
        raise HTTPException(404, f"技能 '{skill_slug}' 不存在")

    ref_path = base / "references" / "source.md"
    return {
        "slug": skill_slug,
        "skill_md": skill_md_path.read_text(encoding="utf-8"),
        "reference": ref_path.read_text(encoding="utf-8") if ref_path.exists() else "",
    }


@router.get("/session/{notebook_id}/manifest")
async def api_get_manifest(nb: NotebookDep):
    """获取 Claude Skills manifest.json。"""
    meta = nb.load_meta() or {}
    doc_name = meta.get("doc_name", "document")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in doc_name)
    manifest_path = Path(config.output_dir) / safe_name / "claude_skills" / "manifest.json"

    if not manifest_path.exists():
        raise HTTPException(404, "尚未生成 Claude Skills")

    return json.loads(manifest_path.read_text(encoding="utf-8"))


@router.post("/session/{notebook_id}/skill-graph")
async def api_skill_graph(nb: NotebookDep):
    """构建 Skill 关系图谱。"""
    skills_data = nb.load_skills()
    if not skills_data:
        raise HTTPException(400, "无已提取的 Skill")

    builder = SkillGraphBuilder()
    builder.build_from_skills(skills_data)
    analysis = builder.analyze()

    return {
        "ok": True, "top_skills": analysis.top_skills,
        "clusters": analysis.clusters, "statistics": analysis.statistics,
        "mermaid": analysis.mermaid, "graph": analysis.graph_json,
    }


@router.get("/skills/registry")
async def api_skill_registry(
    domain: str = Query("", description="按领域过滤"),
    q: str = Query("", description="按触发条件搜索"),
):
    """查询 Skill 注册表。"""
    if q:
        skills = _skill_registry.find_by_trigger(q)
    elif domain:
        skills = _skill_registry.find_by_domain(domain)
    else:
        skills = _skill_registry.list_all()

    return {"total": len(skills), "skills": [s.to_dict() for s in skills]}


@router.get("/skills/search")
async def api_skill_search(
    q: str = Query(..., description="查询文本"),
    top_k: int = Query(5, ge=1, le=50),
):
    """语义检索 Skill（需 Embedding 配置）。"""
    if not _vector_store.is_available:
        raise HTTPException(503, "向量检索不可用：请配置 EMBEDDING_* 参数")

    results = _vector_store.search_similar(q, top_k=top_k)
    return {"query": q, "total": len(results), "results": results}
