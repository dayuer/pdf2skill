"""阶段二：chunk 列表 / 调优 / 历史 / 抽验 / 预览"""

from __future__ import annotations

import random

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .llm_client import AsyncDeepSeekClient, DeepSeekClient
from .skill_extractor import extract_skills_batch, extract_skill_from_chunk
from .skill_validator import SkillValidator
from .notebook_store import FileNotebook
from .api_analyze import get_schema

router = APIRouter(prefix="/api", tags=["tune"])


@router.get("/chunks/{notebook_id}")
async def list_chunks(notebook_id: str, request: Request):
    """chunk 列表，支持分页 + 搜索 + 随机推荐。"""
    nb = FileNotebook(notebook_id)
    chunks = nb.load_chunks()
    if not chunks:
        return JSONResponse({"error": "无 chunk 数据"}, status_code=404)

    params = request.query_params
    q = params.get("q", "").strip()
    recommend = params.get("recommend", "").lower() == "true"
    page = int(params.get("page", "1"))
    page_size = int(params.get("page_size", "20"))

    filtered = chunks
    if q:
        filtered = [c for c in chunks if q in c.content or q in " > ".join(c.heading_path)]

    if recommend:
        step = max(len(filtered) // 5, 1)
        filtered = filtered[::step][:5]
        page, page_size = 1, len(filtered)

    total = len(filtered)
    start = (page - 1) * page_size
    page_items = filtered[start:start + page_size]

    return {
        "total": total, "page": page, "page_size": page_size,
        "items": [
            {"index": c.index, "heading_path": c.heading_path, "char_count": c.char_count,
             "preview": c.content[:100].replace("\n", " "), "text": c.content}
            for c in page_items
        ],
    }


@router.post("/tune/{notebook_id}")
async def tune_chunk(notebook_id: str, request: Request):
    """对单个 chunk 执行提取，返回原文 + 结果，自动写入版本链。"""
    nb = FileNotebook(notebook_id)
    meta = nb.load_meta()
    if not meta:
        return JSONResponse({"error": "笔记本不存在"}, status_code=404)

    body = await request.json()
    chunk_index = body.get("chunk_index", 0)
    prompt_hint = body.get("prompt_hint", "")
    system_prompt_override = body.get("system_prompt", "")

    target = nb.load_chunks_by_indices([chunk_index])
    if not target:
        return JSONResponse({"error": f"chunk {chunk_index} 不存在"}, status_code=404)
    chunk = target[0]

    schema = get_schema(notebook_id, nb)
    client = DeepSeekClient()
    raw_skills = extract_skill_from_chunk(
        chunk, schema, client=client, prompt_hint=prompt_hint,
        system_prompt_override=system_prompt_override,
    )

    validator = SkillValidator()
    src_texts = [chunk.content] * len(raw_skills)
    passed, failed = validator.validate_batch(raw_skills, source_texts=src_texts)

    skills_data = [
        {"name": s.name, "trigger": s.trigger, "domain": s.domain,
         "body": s.body[:800], "status": s.status.value if hasattr(s.status, "value") else str(s.status)}
        for s in passed
    ] + [
        {"name": f.name, "trigger": f.trigger, "domain": f.domain,
         "body": f.body[:800], "status": "failed", "warnings": f.warnings}
        for f in failed
    ]

    version = nb.save_tune_record(
        chunk_index=chunk_index, prompt_hint=prompt_hint,
        extracted_skills=skills_data, source_text=chunk.content,
    )

    return {
        "version": version, "chunk_index": chunk_index,
        "source_text": chunk.content, "source_context": chunk.context,
        "heading_path": chunk.heading_path, "char_count": chunk.char_count,
        "extracted_skills": skills_data, "prompt_hint_used": prompt_hint,
        "passed": len(passed), "failed": len(failed),
    }


@router.get("/tune-history/{notebook_id}")
async def get_tune_history(notebook_id: str):
    """完整调优历史（版本链）。"""
    return FileNotebook(notebook_id).load_tune_history()


@router.post("/sample-check/{notebook_id}")
async def sample_check(notebook_id: str, request: Request):
    """随机抽样验证：选 N 个 chunk → 批量提取 → 返回逐条对比。"""
    nb = FileNotebook(notebook_id)
    meta = nb.load_meta()
    if not meta:
        return JSONResponse({"error": "笔记本不存在"}, status_code=404)

    body = await request.json()
    sample_size = body.get("sample_size", 5)

    chunks = nb.load_chunks()
    schema = get_schema(notebook_id, nb)
    prompt_hint = nb.get_active_prompt_hint()
    sample = random.sample(chunks, min(sample_size, len(chunks)))

    async_client = AsyncDeepSeekClient()
    raw_skills = await extract_skills_batch(sample, schema, client=async_client, prompt_hint=prompt_hint)

    validator = SkillValidator()
    source_map = {c.index: c.content for c in sample}
    src_texts = [source_map.get(rs.source_chunk_index) for rs in raw_skills]
    passed, failed = validator.validate_batch(raw_skills, source_texts=src_texts)

    results_by_chunk: dict[int, dict] = {}
    for c in sample:
        results_by_chunk[c.index] = {
            "chunk_index": c.index, "heading_path": c.heading_path,
            "source_preview": c.content[:200], "skills": [],
        }
    for s in passed:
        if s.source_chunk_index in results_by_chunk:
            results_by_chunk[s.source_chunk_index]["skills"].append(
                {"name": s.name, "trigger": s.trigger, "status": "pass"})
    for f in failed:
        if f.source_chunk_index in results_by_chunk:
            results_by_chunk[f.source_chunk_index]["skills"].append(
                {"name": f.name, "trigger": f.trigger, "status": "failed"})

    return {
        "sample_size": len(sample), "total_raw": len(raw_skills),
        "passed": len(passed), "failed": len(failed),
        "pass_rate": round(len(passed) / max(len(raw_skills), 1) * 100, 1),
        "prompt_hint_used": prompt_hint,
        "results": list(results_by_chunk.values()),
    }


@router.post("/preview/{notebook_id}")
async def preview_sample(notebook_id: str, sample_size: int = 5):
    """采样 N 个 chunk → 提取样本 Skill → 写盘 + 返回预览。"""
    nb = FileNotebook(notebook_id)
    meta = nb.load_meta()
    if not meta:
        return JSONResponse({"error": "笔记本不存在"}, status_code=404)

    chunks = nb.load_chunks()
    schema = get_schema(notebook_id, nb)

    if len(chunks) <= sample_size:
        sample = chunks
    else:
        step = len(chunks) / sample_size
        sample = [chunks[int(i * step)] for i in range(sample_size)]

    async_client = AsyncDeepSeekClient()
    raw_skills = await extract_skills_batch(sample, schema, client=async_client)

    validator = SkillValidator()
    source_map = {c.index: c.content for c in sample}
    raw_source_texts = [source_map.get(rs.source_chunk_index) for rs in raw_skills]
    passed, failed = validator.validate_batch(raw_skills, source_texts=raw_source_texts)

    for i, s in enumerate(passed):
        nb.save_skill(s, idx=i)
    nb.save_status(
        phase="previewed", total=len(chunks),
        raw_skills=len(raw_skills), passed=len(passed), failed=len(failed),
    )

    return {
        "sample_chunks": len(sample), "raw_skills": len(raw_skills),
        "passed": len(passed), "failed": len(failed),
        "skills": [
            {"name": s.name, "trigger": s.trigger, "domain": s.domain,
             "body": s.body[:500], "source_context": s.source_context}
            for s in passed
        ],
        "failed_details": [{"name": f.name, "warnings": f.warnings} for f in failed[:3]],
    }
