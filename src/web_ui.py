"""
pdf2skill Web UI — 三阶段交互式 Pipeline

阶段一：上传文档 → 自动识别类型 → 展示 Schema + 推荐 Prompt
阶段二：采样 5 个 chunk → 提取样本 Skill → 用户预览确认
阶段三：用户确认后 → 全量执行，SSE 实时推送进度和结果

技术栈：FastAPI + SSE + 原生 HTML/JS（零前端依赖）
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from .config import config, PipelineConfig
from .document_loader import load_document, LoadResult
from .llm_client import AsyncDeepSeekClient, DeepSeekClient
from .markdown_chunker import chunk_markdown, ChunkResult, TextChunk
from .schema_generator import SkillSchema, generate_schema
from .semantic_filter import filter_chunks
from .skill_extractor import (
    extract_skills_batch, extract_skill_from_chunk, _resolve_prompt_type,
    generate_baseline_hint, get_system_prompt_preview,
)
from .skill_validator import SkillValidator, ValidatedSkill, RawSkill
from .skill_reducer import cluster_skills, reduce_all_clusters
from .skill_packager import package_skills
from .session_store import FileSession, list_sessions as list_disk_sessions

app = FastAPI(title="pdf2skill", version="0.3")

# 上传目录
_UPLOAD_DIR = Path("uploads")
_UPLOAD_DIR.mkdir(exist_ok=True)

# 内存缓存（仅缓存 schema 对象，避免重复反序列化）
_schema_cache: dict[str, SkillSchema] = {}


# ──── API ────


@app.post("/api/analyze")
async def analyze_document(file: UploadFile = File(...)):
    """
    阶段一：上传文档 → 类型检测 → Schema 生成 → 返回分析结果。

    返回：文档类型、领域、推荐 Prompt、chunk 统计。
    """
    # 保存上传文件
    session_id = str(uuid.uuid4())[:8]
    original_name = file.filename or "doc"
    session_dir = _UPLOAD_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    saved_path = session_dir / original_name
    content = await file.read()
    saved_path.write_bytes(content)

    # 加载文档
    load_result = load_document(str(saved_path))

    # 切分
    chunk_result = chunk_markdown(
        load_result.markdown,
        load_result.doc_name,
        split_level=config.chunk.split_level,
    )

    # 语义粗筛
    filter_result = filter_chunks(chunk_result.chunks)

    # Schema 生成
    sync_client = DeepSeekClient()
    schema = generate_schema(
        load_result.markdown, load_result.doc_name, client=sync_client
    )

    # 解析推荐 Prompt
    prompt_name, user_template = _resolve_prompt_type(schema.book_type)

    # 持久化到磁盘
    fs = FileSession(session_id)
    fs.save_meta(
        doc_name=load_result.doc_name,
        format=load_result.format.value,
        filepath=str(saved_path),
        book_type=schema.book_type,
        domains=schema.domains,
        total_chunks=len(chunk_result.chunks),
        filtered_chunks=len(filter_result.kept),
        prompt_type=prompt_name,
        core_components=schema.fields.get("core_components", []),
        skill_types=schema.fields.get("skill_types", []),
    )
    fs.save_schema(schema)
    fs.save_chunks(filter_result.kept)
    fs.save_status(phase="analyzed", total=len(filter_result.kept))

    # 缓存 schema 对象
    _schema_cache[session_id] = schema

    return {
        "session_id": session_id,
        "doc_name": load_result.doc_name,
        "format": load_result.format.value,
        "book_type": schema.book_type,
        "domains": schema.domains,
        "total_chunks": len(chunk_result.chunks),
        "filtered_chunks": len(filter_result.kept),
        "dropped_chunks": filter_result.dropped_count,
        "prompt_type": prompt_name,
        "schema_constraint": schema.to_prompt_constraint()[:500],
        "core_components": schema.fields.get("core_components", []),
        "skill_types": schema.fields.get("skill_types", []),
        "baseline_hint": generate_baseline_hint(schema.book_type),
        "system_prompt": get_system_prompt_preview(
            schema.book_type, schema.to_prompt_constraint()
        ),
    }


@app.put("/api/session/{session_id}/settings")
async def update_session_settings(session_id: str, request: Request):
    """
    用户调整提取设置（文档类型、提取策略）。
    在预览/执行前调用。
    """
    fs = FileSession(session_id)
    meta = fs.load_meta()
    if not meta:
        return JSONResponse({"error": "会话不存在"}, status_code=404)

    body = await request.json()
    new_book_type = body.get("book_type", meta.get("book_type", ""))
    new_prompt_type = body.get("prompt_type", meta.get("prompt_type", ""))

    # 更新 meta
    meta["book_type"] = new_book_type
    meta["prompt_type"] = new_prompt_type
    fs._write_json("meta.json", meta)

    # 更新 schema
    schema_data = fs.load_schema() or {}
    schema_data["book_type"] = new_book_type
    fs._write_json("schema.json", schema_data)

    # 刷新内存缓存
    if session_id in _schema_cache:
        _schema_cache[session_id].book_type = new_book_type

    return {"ok": True, "book_type": new_book_type, "prompt_type": new_prompt_type}


@app.get("/api/prompt-preview/{session_id}")
async def prompt_preview(session_id: str):
    """返回当前的完整 system prompt + 基线 hint，用于前端展示。"""
    fs = FileSession(session_id)
    meta = fs.load_meta()
    if not meta:
        return JSONResponse({"error": "会话不存在"}, status_code=404)

    schema = _get_schema(session_id, fs)
    book_type = meta.get("book_type", "技术手册")
    constraint = schema.to_prompt_constraint() if schema else ""

    return {
        "system_prompt": get_system_prompt_preview(book_type, constraint),
        "baseline_hint": generate_baseline_hint(book_type),
        "book_type": book_type,
        "prompt_type": meta.get("prompt_type", ""),
    }


# ──── 阶段 2：深度调优 API ────


@app.get("/api/chunks/{session_id}")
async def list_chunks(session_id: str, request: Request):
    """
    返回 chunk 摘要列表，支持分页 + 搜索 + 随机推荐。
    参数: page=1, page_size=20, q=搜索关键词, recommend=true（随机推荐5个）
    """
    fs = FileSession(session_id)
    chunks = fs.load_chunks()
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
        # 随机推荐：均匀分布取 5 个代表性 chunk
        step = max(len(filtered) // 5, 1)
        filtered = filtered[::step][:5]
        page, page_size = 1, len(filtered)

    total = len(filtered)
    start = (page - 1) * page_size
    page_items = filtered[start:start + page_size]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "index": c.index,
                "heading_path": c.heading_path,
                "char_count": c.char_count,
                "preview": c.content[:100].replace("\n", " "),
            }
            for c in page_items
        ],
    }


@app.post("/api/tune/{session_id}")
async def tune_chunk(session_id: str, request: Request):
    """
    对指定单个 chunk 执行提取，返回原文 + 提取结果。
    支持 prompt_hint 调优指令，每次调用自动写入版本链。
    """
    fs = FileSession(session_id)
    meta = fs.load_meta()
    if not meta:
        return JSONResponse({"error": "会话不存在"}, status_code=404)

    body = await request.json()
    chunk_index = body.get("chunk_index", 0)
    prompt_hint = body.get("prompt_hint", "")

    # 加载指定 chunk
    target = fs.load_chunks_by_indices([chunk_index])
    if not target:
        return JSONResponse({"error": f"chunk {chunk_index} 不存在"}, status_code=404)
    chunk = target[0]

    # Schema
    schema = _get_schema(session_id, fs)

    # 同步提取（单 chunk，无需异步并发）
    client = DeepSeekClient()
    raw_skills = extract_skill_from_chunk(
        chunk, schema, client=client, prompt_hint=prompt_hint
    )

    # 校验
    validator = SkillValidator()
    src_texts = [chunk.content] * len(raw_skills)
    passed, failed = validator.validate_batch(raw_skills, source_texts=src_texts)

    # 构建结果
    skills_data = [
        {
            "name": s.name,
            "trigger": s.trigger,
            "domain": s.domain,
            "body": s.body[:800],
            "status": s.status.value if hasattr(s.status, "value") else str(s.status),
        }
        for s in passed
    ] + [
        {
            "name": f.name,
            "trigger": f.trigger,
            "domain": f.domain,
            "body": f.body[:800],
            "status": "failed",
            "warnings": f.warnings,
        }
        for f in failed
    ]

    # 写入版本链
    version = fs.save_tune_record(
        chunk_index=chunk_index,
        prompt_hint=prompt_hint,
        extracted_skills=skills_data,
        source_text=chunk.content,
    )

    return {
        "version": version,
        "chunk_index": chunk_index,
        "source_text": chunk.content,
        "source_context": chunk.context,
        "heading_path": chunk.heading_path,
        "char_count": chunk.char_count,
        "extracted_skills": skills_data,
        "prompt_hint_used": prompt_hint,
        "passed": len(passed),
        "failed": len(failed),
    }


@app.get("/api/tune-history/{session_id}")
async def get_tune_history(session_id: str):
    """返回完整调优历史（版本链）。"""
    fs = FileSession(session_id)
    return fs.load_tune_history()


@app.post("/api/sample-check/{session_id}")
async def sample_check(session_id: str, request: Request):
    """
    随机抽样验证：随机选 N 个 chunk → 批量提取 → 返回逐条对比结果。
    使用最后确认的 prompt_hint。
    """
    fs = FileSession(session_id)
    meta = fs.load_meta()
    if not meta:
        return JSONResponse({"error": "会话不存在"}, status_code=404)

    body = await request.json()
    sample_size = body.get("sample_size", 5)

    chunks = fs.load_chunks()
    schema = _get_schema(session_id, fs)
    prompt_hint = fs.get_active_prompt_hint()

    # 随机抽样
    sample = random.sample(chunks, min(sample_size, len(chunks)))

    # 异步批量提取
    async_client = AsyncDeepSeekClient()
    raw_skills = await extract_skills_batch(
        sample, schema, client=async_client, prompt_hint=prompt_hint
    )

    # 校验
    validator = SkillValidator()
    source_map = {c.index: c.content for c in sample}
    src_texts = [source_map.get(rs.source_chunk_index) for rs in raw_skills]
    passed, failed = validator.validate_batch(raw_skills, source_texts=src_texts)

    # 按 chunk 分组组织结果
    results_by_chunk: dict[int, dict] = {}
    for c in sample:
        results_by_chunk[c.index] = {
            "chunk_index": c.index,
            "heading_path": c.heading_path,
            "source_preview": c.content[:200],
            "skills": [],
        }
    for s in passed:
        if s.source_chunk_index in results_by_chunk:
            results_by_chunk[s.source_chunk_index]["skills"].append({
                "name": s.name, "trigger": s.trigger, "status": "pass",
            })
    for f in failed:
        if f.source_chunk_index in results_by_chunk:
            results_by_chunk[f.source_chunk_index]["skills"].append({
                "name": f.name, "trigger": f.trigger, "status": "failed",
            })

    return {
        "sample_size": len(sample),
        "total_raw": len(raw_skills),
        "passed": len(passed),
        "failed": len(failed),
        "pass_rate": round(len(passed) / max(len(raw_skills), 1) * 100, 1),
        "prompt_hint_used": prompt_hint,
        "results": list(results_by_chunk.values()),
    }

@app.post("/api/preview/{session_id}")
async def preview_sample(session_id: str, sample_size: int = 5):
    """
    阶段二：采样 N 个 chunk → 提取样本 Skill → 写盘 + 返回预览。
    """
    fs = FileSession(session_id)
    meta = fs.load_meta()
    if not meta:
        return JSONResponse({"error": "会话不存在"}, status_code=404)

    chunks = fs.load_chunks()
    schema = _get_schema(session_id, fs)

    # 均匀采样
    if len(chunks) <= sample_size:
        sample = chunks
    else:
        step = len(chunks) / sample_size
        sample = [chunks[int(i * step)] for i in range(sample_size)]

    # 提取样本
    async_client = AsyncDeepSeekClient()
    raw_skills = await extract_skills_batch(sample, schema, client=async_client)

    # 校验
    validator = SkillValidator()
    source_map = {c.index: c.content for c in sample}
    raw_source_texts = [source_map.get(rs.source_chunk_index) for rs in raw_skills]
    passed, failed = validator.validate_batch(raw_skills, source_texts=raw_source_texts)

    # 每个 Skill 立即写盘
    for i, s in enumerate(passed):
        fs.save_skill(s, idx=i)
    fs.save_status(
        phase="previewed",
        total=len(chunks),
        raw_skills=len(raw_skills),
        passed=len(passed),
        failed=len(failed),
    )

    return {
        "sample_chunks": len(sample),
        "raw_skills": len(raw_skills),
        "passed": len(passed),
        "failed": len(failed),
        "skills": [
            {
                "name": s.name,
                "trigger": s.trigger,
                "domain": s.domain,
                "body": s.body[:500],
                "source_context": s.source_context,
            }
            for s in passed
        ],
        "failed_details": [
            {"name": f.name, "warnings": f.warnings}
            for f in failed[:3]
        ],
    }


@app.get("/api/execute/{session_id}")
async def execute_full(request: Request, session_id: str):
    """
    阶段三：SSE 全量执行（S/L 断点续传）。

    自动检测已处理的 chunk，跳过它们，从断点继续。
    每批完成后立即写盘 + 更新 progress_index.json。
    断开连接 → 自动存档；再次调用 → 自动读档继续。
    """
    fs = FileSession(session_id)
    meta = fs.load_meta()
    if not meta:
        return JSONResponse({"error": "会话不存在"}, status_code=404)

    async def event_generator():
        schema = _get_schema(session_id, fs)
        prompt_hint = fs.get_active_prompt_hint()
        doc_name = meta["doc_name"]
        total = fs.chunk_count()
        skill_idx = fs.skill_count()

        # ── S/L：检测断点 ──
        pending = fs.get_pending_chunk_indices(total)
        done_count = total - len(pending)

        if done_count > 0:
            yield {
                "event": "phase",
                "data": json.dumps({
                    "phase": "resume",
                    "message": f"📂 读档：已完成 {done_count}/{total}，从断点继续剩余 {len(pending)} 块",
                    "total": total,
                    "done": done_count,
                }),
            }
        else:
            yield {
                "event": "phase",
                "data": json.dumps({
                    "phase": "extraction",
                    "message": f"开始全量提取：{total} 个文本块",
                    "total": total,
                }),
            }

        if not pending:
            # 全部已完成，直接返回结果
            all_skills_data = fs.load_skills()
            yield {
                "event": "complete",
                "data": json.dumps({
                    "final_skills": len(all_skills_data),
                    "output_dir": f"sessions/{session_id}/skills/",
                    "skills": [
                        {
                            "name": s.get("name", ""),
                            "trigger": s.get("trigger", ""),
                            "domain": s.get("domain", ""),
                            "body": s.get("body", "")[:300],
                        }
                        for s in all_skills_data[:30]
                    ],
                    "elapsed_s": 0,
                    "resumed": True,
                }),
            }
            return

        async_client = AsyncDeepSeekClient()
        raw_count = 0
        completed = done_count  # 从断点计数
        t_start = time.monotonic()

        # ── 分批处理 pending chunks ──
        batch_size = 5
        for batch_offset in range(0, len(pending), batch_size):
            if await request.is_disconnected():
                # 断开 → 自动存档（progress_index 已在上一批写入）
                fs.save_status(
                    phase="paused",
                    completed=completed, total=total,
                    raw_skills=raw_count, passed=skill_idx,
                    elapsed_s=time.monotonic() - t_start,
                )
                return

            # 只加载本批需要的 chunk（最小内存）
            batch_indices = pending[batch_offset:batch_offset + batch_size]
            batch_chunks = fs.load_chunks_by_indices(batch_indices)

            batch_skills = await extract_skills_batch(
                batch_chunks, schema, client=async_client,
                prompt_hint=prompt_hint,
            )
            raw_count += len(batch_skills)
            completed += len(batch_chunks)

            # 立即校验 + 写盘
            if batch_skills:
                validator = SkillValidator()
                source_map = {c.index: c.content for c in batch_chunks}
                src_texts = [source_map.get(rs.source_chunk_index) for rs in batch_skills]
                passed_batch, _ = validator.validate_batch(batch_skills, source_texts=src_texts)
                for s in passed_batch:
                    fs.save_skill(s, idx=skill_idx)
                    skill_idx += 1

            # ── S/L 存档：标记本批 chunk 完成 ──
            fs.mark_chunks_done([c.index for c in batch_chunks])

            elapsed = time.monotonic() - t_start
            pending_left = total - completed
            eta = (pending_left / (completed - done_count) * elapsed) if completed > done_count else 0

            fs.save_status(
                phase="extracting",
                completed=completed, total=total,
                raw_skills=raw_count, passed=skill_idx,
                elapsed_s=elapsed,
            )

            yield {
                "event": "progress",
                "data": json.dumps({
                    "completed": completed,
                    "total": total,
                    "raw_skills": raw_count,
                    "skills_on_disk": skill_idx,
                    "elapsed_s": round(elapsed, 1),
                    "eta_s": round(eta, 1),
                    "latest_skills": [
                        {"name": s.raw_text[:100], "source": s.source_context}
                        for s in batch_skills[:3]
                    ],
                }),
            }

        # ── 全部完成 ──
        elapsed_total = time.monotonic() - t_start
        fs.save_status(
            phase="complete",
            completed=total, total=total,
            raw_skills=raw_count, passed=skill_idx,
            final_skills=skill_idx, elapsed_s=elapsed_total,
        )

        all_skills_data = fs.load_skills()
        yield {
            "event": "complete",
            "data": json.dumps({
                "final_skills": len(all_skills_data),
                "output_dir": f"sessions/{session_id}/skills/",
                "skills": [
                    {
                        "name": s.get("name", ""),
                        "trigger": s.get("trigger", ""),
                        "domain": s.get("domain", ""),
                        "body": s.get("body", "")[:300],
                    }
                    for s in all_skills_data[:30]
                ],
                "elapsed_s": round(elapsed_total, 1),
            }),
        }

    return EventSourceResponse(event_generator())


@app.get("/api/sessions")
async def api_list_sessions():
    """列出所有持久化的会话"""
    return list_disk_sessions()


@app.get("/api/session/{session_id}/skills")
async def api_session_skills(session_id: str):
    """获取会话中已提取的所有 Skill"""
    fs = FileSession(session_id)
    return fs.load_skills()


@app.get("/api/session/{session_id}/state")
async def api_session_state(session_id: str):
    """
    获取会话完整状态（用于页面刷新后恢复 UI）。

    返回 meta + status + skills 摘要，前端据此还原到正确阶段。
    """
    fs = FileSession(session_id)
    meta = fs.load_meta()
    if not meta:
        return JSONResponse({"error": "会话不存在"}, status_code=404)

    status = fs.load_status() or {}
    skills = fs.load_skills()
    total = fs.chunk_count()
    done = fs.get_done_count()

    return {
        "session_id": session_id,
        "meta": meta,
        "status": status,
        "total_chunks": total,
        "done_chunks": done,
        "pending_chunks": total - done,
        "skills_on_disk": len(skills),
        "skills_preview": [
            {
                "name": s.get("name", ""),
                "trigger": s.get("trigger", ""),
                "domain": s.get("domain", ""),
                "body": s.get("body", "")[:500],
                "source_context": s.get("source_context", ""),
            }
            for s in skills[:10]
        ],
    }


def _get_schema(session_id: str, fs: FileSession) -> SkillSchema:
    """获取 Schema（优先内存缓存，否则从磁盘重建）"""
    if session_id in _schema_cache:
        return _schema_cache[session_id]
    schema_data = fs.load_schema()
    if not schema_data:
        raise ValueError(f"会话 {session_id} 的 Schema 不存在")
    schema = SkillSchema(
        book_type=schema_data["book_type"],
        domains=schema_data["domains"],
    )
    _schema_cache[session_id] = schema
    return schema


# ──── 前端页面 ────


@app.get("/", response_class=HTMLResponse)
async def index():
    return _HTML_PAGE


_HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>pdf2skill — 智能文档解析</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, 'SF Pro Display', 'Inter', sans-serif;
    background: #0a0a0f; color: #e4e4e7; min-height: 100vh;
  }
  .container { max-width: 1100px; margin: 0 auto; padding: 40px 24px; }
  h1 {
    font-size: 32px; font-weight: 700;
    background: linear-gradient(135deg, #7c3aed, #06b6d4);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
  }
  .subtitle { color: #71717a; font-size: 14px; margin-bottom: 32px; }

  .phase-card {
    background: #18181b; border: 1px solid #27272a;
    border-radius: 16px; padding: 24px; margin-bottom: 20px;
    transition: all 0.3s;
  }
  .phase-card.active { border-color: #7c3aed; box-shadow: 0 0 20px rgba(124,58,237,0.15); }
  .phase-card.done { border-color: #22c55e; opacity: 0.85; }
  .phase-card.hidden { display: none; }
  .phase-card.collapsed > *:not(.phase-header) { display: none !important; }
  .phase-header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; position: relative; }
  .phase-toggle {
    position: absolute; right: 0; top: 0;
    background: none; border: none; color: #71717a; font-size: 18px;
    cursor: pointer; padding: 4px 8px; transition: transform 0.2s;
  }
  .phase-toggle:hover { color: #c084fc; }
  .phase-card.collapsed .phase-toggle { transform: rotate(180deg); }
  .phase-card.collapsed .phase-header { margin-bottom: 0; }
  .phase-number {
    width: 32px; height: 32px; border-radius: 50%;
    background: #27272a; color: #71717a;
    display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 14px;
  }
  .phase-card.active .phase-number { background: #7c3aed; color: #fff; }
  .phase-card.done .phase-number { background: #22c55e; color: #fff; }
  .phase-title { font-size: 18px; font-weight: 600; }

  .upload-zone {
    border: 2px dashed #3f3f46; border-radius: 12px;
    padding: 40px; text-align: center; cursor: pointer; transition: all 0.3s;
  }
  .upload-zone:hover { border-color: #7c3aed; background: rgba(124,58,237,0.05); }
  .upload-zone.dragover { border-color: #7c3aed; background: rgba(124,58,237,0.1); }
  .upload-icon { font-size: 48px; margin-bottom: 12px; }
  .upload-text { color: #a1a1aa; font-size: 14px; }
  input[type=file] { display: none; }

  .info-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 12px; margin: 16px 0;
  }
  .info-item { background: #1f1f23; border-radius: 8px; padding: 12px; }
  .info-label { color: #71717a; font-size: 12px; margin-bottom: 4px; }
  .info-value { font-size: 16px; font-weight: 600; }
  .info-value.highlight { color: #7c3aed; }

  .summary-section {
    background: #1f1f23; border-radius: 10px; padding: 14px 16px;
    margin: 12px 0 4px; border-left: 3px solid #7c3aed;
  }
  .summary-section .summary-title { font-size: 12px; color: #71717a; margin-bottom: 8px; font-weight: 600; }
  .summary-tags { display: flex; flex-wrap: wrap; gap: 6px; }
  .summary-tag { display: inline-block; padding: 3px 10px; border-radius: 4px; font-size: 12px; background: rgba(124,58,237,0.15); color: #c084fc; }
  .summary-tag.green { background: rgba(34,197,94,0.15); color: #4ade80; }

  .setting-select {
    width: 100%; padding: 8px 10px; margin-top: 4px;
    background: #27272a; color: #e4e4e7; border: 1px solid #3f3f46;
    border-radius: 6px; font-size: 14px; font-weight: 600; cursor: pointer; outline: none;
  }
  .setting-select:focus { border-color: #7c3aed; }
  .setting-select option { background: #18181b; color: #e4e4e7; }

  .skill-card {
    background: #1f1f23; border: 1px solid #27272a;
    border-radius: 12px; padding: 16px; margin: 8px 0;
  }
  .skill-name { font-weight: 600; color: #c084fc; margin-bottom: 4px; }
  .skill-trigger { color: #a1a1aa; font-size: 13px; margin-bottom: 8px; }
  .skill-domain { display: inline-block; background: rgba(124,58,237,0.2); color: #c084fc; padding: 2px 8px; border-radius: 4px; font-size: 11px; margin-right: 6px; }
  .skill-body { font-size: 13px; color: #a1a1aa; margin-top: 8px; white-space: pre-wrap; line-height: 1.6; max-height: 200px; overflow-y: auto; }

  .btn { padding: 10px 24px; border-radius: 8px; border: none; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.2s; }
  .btn-primary { background: linear-gradient(135deg, #7c3aed, #6d28d9); color: #fff; }
  .btn-primary:hover { opacity: 0.9; transform: translateY(-1px); }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
  .btn-ghost { background: transparent; border: 1px solid #3f3f46; color: #a1a1aa; }
  .btn-ghost:hover { border-color: #7c3aed; color: #c084fc; }
  .btn-sm { padding: 6px 14px; font-size: 12px; }

  .progress-bar { width: 100%; height: 6px; background: #27272a; border-radius: 3px; overflow: hidden; margin: 12px 0; }
  .progress-fill { height: 100%; border-radius: 3px; background: linear-gradient(90deg, #7c3aed, #06b6d4); transition: width 0.5s ease; width: 0%; }
  .progress-text { font-size: 13px; color: #71717a; }

  .spinner { display: inline-block; width: 20px; height: 20px; border: 2px solid #3f3f46; border-top-color: #7c3aed; border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .loading-text { display: flex; align-items: center; gap: 10px; color: #a1a1aa; }

  /* 调优面板 */
  .tune-panel { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 16px 0; }
  .source-pane, .result-pane { background: #1f1f23; border-radius: 10px; padding: 16px; max-height: 500px; overflow-y: auto; }
  .pane-title { font-size: 13px; font-weight: 700; margin-bottom: 10px; display: flex; align-items: center; gap: 6px; }
  .pane-title .dot { width: 8px; height: 8px; border-radius: 50%; }
  .pane-title .dot.source { background: #06b6d4; }
  .pane-title .dot.result { background: #7c3aed; }
  .source-text { font-size: 13px; color: #d4d4d8; white-space: pre-wrap; line-height: 1.7; font-family: 'SF Mono', monospace; }

  .tune-textarea {
    width: 100%; min-height: 80px; padding: 12px;
    background: #1f1f23; color: #e4e4e7; border: 1px solid #3f3f46;
    border-radius: 8px; font-size: 14px; resize: vertical; font-family: inherit; outline: none;
  }
  .tune-textarea:focus { border-color: #7c3aed; }
  .tune-textarea::placeholder { color: #52525b; }

  .version-timeline { display: flex; gap: 8px; flex-wrap: wrap; margin: 12px 0; }
  .version-chip {
    padding: 4px 12px; border-radius: 20px; font-size: 12px;
    cursor: pointer; transition: all 0.2s;
    background: #27272a; color: #71717a; border: 1px solid #3f3f46;
  }
  .version-chip:hover { border-color: #7c3aed; color: #c084fc; }
  .version-chip.active { background: rgba(124,58,237,0.2); color: #c084fc; border-color: #7c3aed; }

  .chunk-select {
    width: 100%; padding: 8px 10px; margin-bottom: 12px;
    background: #27272a; color: #e4e4e7; border: 1px solid #3f3f46;
    border-radius: 6px; font-size: 13px; cursor: pointer; outline: none;
  }
  .chunk-select:focus { border-color: #7c3aed; }

  .sample-item { background: #1f1f23; border-radius: 10px; padding: 14px; margin: 8px 0; border-left: 3px solid #3f3f46; }
  .sample-item.pass { border-left-color: #22c55e; }
  .sample-item.fail { border-left-color: #ef4444; }
  .sample-heading { font-size: 13px; font-weight: 600; color: #a1a1aa; margin-bottom: 6px; }
  .sample-preview { font-size: 12px; color: #71717a; margin-bottom: 8px; }
  .sample-skills { display: flex; flex-wrap: wrap; gap: 6px; }
  .sample-skill-tag { padding: 2px 8px; border-radius: 4px; font-size: 11px; background: rgba(124,58,237,0.15); color: #c084fc; }
  .sample-skill-tag.fail { background: rgba(239,68,68,0.15); color: #f87171; }
</style>
</head>
<body>
<div class="container">
  <h1>pdf2skill</h1>
  <p class="subtitle">智能文档解析 → 结构化知识提取</p>

  <!-- 阶段 1 -->
  <div id="phase1" class="phase-card active">
    <div class="phase-header"><div class="phase-number">1</div><div class="phase-title">上传文档 · 类型检测</div><button class="phase-toggle" onclick="togglePhase('phase1')">▲</button></div>
    <div id="upload-area">
      <div class="upload-zone" id="dropzone" onclick="document.getElementById('fileInput').click()">
        <div class="upload-icon">📄</div>
        <div class="upload-text">拖拽文件到此处，或点击选择<br>支持 PDF / TXT / EPUB</div>
      </div>
      <input type="file" id="fileInput" accept=".pdf,.txt,.epub,.md">
    </div>
    <div id="analysis-loading" style="display:none" class="loading-text"><div class="spinner"></div><span>R1 正在分析文档类型和知识结构...</span></div>
    <div id="analysis-result" style="display:none"></div>
    <div id="reupload-wrap" style="display:none; margin-top:12px; text-align:right">
      <button class="btn btn-ghost btn-sm" onclick="resetSession()">📄 重新上传文件</button>
    </div>
  </div>

  <!-- 阶段 2：深度调优 -->
  <div id="phase2" class="phase-card hidden">
    <div class="phase-header"><div class="phase-number">2</div><div class="phase-title">深度调优 · 原文对比</div><button class="phase-toggle" onclick="togglePhase('phase2')">▲</button></div>
    <div id="tune-controls">
      <div style="display:flex; gap:12px; align-items:center; margin-bottom:8px">
        <label style="font-size:13px; color:#71717a; white-space:nowrap">选择文本块</label>
        <input id="chunk-search" class="chunk-select" style="flex:1" placeholder="搜索关键词…（留空使用系统推荐）" oninput="searchChunks()">
        <span id="chunk-total" style="font-size:12px; color:#52525b; white-space:nowrap"></span>
      </div>
      <select id="chunk-select" class="chunk-select" size="5" style="height:auto; min-height:80px"></select>
    </div>
    <details id="prompt-details" style="margin:12px 0">
      <summary style="font-size:12px; color:#7c3aed; cursor:pointer; user-select:none">🔍 查看当前系统 Prompt（点击展开）</summary>
      <div id="system-prompt-display" class="source-text" style="background:#1f1f23; padding:12px; border-radius:8px; margin-top:8px; max-height:300px; overflow-y:auto; font-size:12px"></div>
    </details>
    <div id="tune-loading" style="display:none" class="loading-text"><div class="spinner"></div><span>R1 正在提取...</span></div>
    <div id="tune-result" style="display:none"></div>
    <div style="margin-top:12px">
      <label style="font-size:13px; color:#71717a">Prompt 调优方向（系统已根据文档类型预填基线策略，可修改）</label>
      <textarea id="prompt-hint" class="tune-textarea" placeholder="加载中..."></textarea>
      <div style="margin-top:10px; display:flex; gap:12px; align-items:center;">
        <button class="btn btn-primary" onclick="runTune()">🔬 提取并对比</button>
        <button class="btn btn-ghost btn-sm" onclick="goToSampleCheck()">✅ 调优完成，进入抽样验证</button>
      </div>
    </div>
    <div id="version-timeline-wrap" style="display:none; margin-top:16px">
      <label style="font-size:12px; color:#71717a;">版本历史（点击回溯）</label>
      <div id="version-timeline" class="version-timeline"></div>
    </div>
  </div>

  <!-- 阶段 3：随机抽样验证 -->
  <div id="phase3" class="phase-card hidden">
    <div class="phase-header"><div class="phase-number">3</div><div class="phase-title">随机抽样验证</div><button class="phase-toggle" onclick="togglePhase('phase3')">▲</button></div>
    <div style="display:flex; gap:12px; align-items:center;">
      <button class="btn btn-primary" onclick="runSampleCheck()">🎲 随机抽样 5 个 chunk</button>
      <button class="btn btn-ghost btn-sm" onclick="goToExecute()">⚡ 跳过验证，直接全量</button>
    </div>
    <div id="sample-loading" style="display:none" class="loading-text"><div class="spinner"></div><span>R1 正在批量提取和校验...</span></div>
    <div id="sample-result" style="display:none"></div>
  </div>

  <!-- 阶段 4：全量执行 -->
  <div id="phase4" class="phase-card hidden">
    <div class="phase-header"><div class="phase-number">4</div><div class="phase-title">全量执行 · 实时结果</div><button class="phase-toggle" onclick="togglePhase('phase4')">▲</button></div>
    <div id="execute-progress" style="display:none"></div>
    <div id="execute-result" style="display:none"></div>
  </div>
</div>

<script>
let sessionId = localStorage.getItem('pdf2skill_session');

function togglePhase(id) {
  document.getElementById(id).classList.toggle('collapsed');
}

function resetSession() {
  localStorage.removeItem('pdf2skill_session');
  location.reload();
}

// ── 上传 ──
const fileInput = document.getElementById('fileInput');
const dropzone = document.getElementById('dropzone');
dropzone.addEventListener('dragover', e => { e.preventDefault(); dropzone.classList.add('dragover'); });
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
dropzone.addEventListener('drop', e => { e.preventDefault(); dropzone.classList.remove('dragover'); if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]); });
fileInput.addEventListener('change', () => { if (fileInput.files[0]) uploadFile(fileInput.files[0]); });

async function uploadFile(file) {
  document.getElementById('upload-area').style.display = 'none';
  document.getElementById('analysis-loading').style.display = 'flex';
  const fd = new FormData(); fd.append('file', file);
  try {
    const r = await fetch('/api/analyze', { method: 'POST', body: fd });
    const d = await r.json();
    sessionId = d.session_id;
    localStorage.setItem('pdf2skill_session', sessionId);
    showAnalysis(d);
  } catch (e) {
    alert('分析失败: ' + e.message);
    document.getElementById('upload-area').style.display = 'block';
    document.getElementById('analysis-loading').style.display = 'none';
  }
}

function showAnalysis(data) {
  document.getElementById('analysis-loading').style.display = 'none';
  const el = document.getElementById('analysis-result');
  el.style.display = 'block';
  const typeOpts = ['技术手册','叙事类','方法论','学术教材','操作规范'].map(t =>
    `<option value="${t}" ${t===data.book_type?'selected':''}>${t}</option>`).join('');
  const pm = {'技术手册':'extractor','叙事类':'narrative_extractor','方法论':'methodology_extractor','学术教材':'academic_extractor','操作规范':'extractor'};
  const promptOpts = Object.entries(pm).map(([l,v]) =>
    `<option value="${v}" ${v===data.prompt_type?'selected':''}>${v} (${l})</option>`).join('');
  const cc = (data.core_components||[]).map(c=>`<span class="summary-tag">${c}</span>`).join('');
  const st = (data.skill_types||[]).map(c=>`<span class="summary-tag green">${c}</span>`).join('');

  el.innerHTML = `
    <div class="info-grid">
      <div class="info-item"><div class="info-label">文档名称</div><div class="info-value">${data.doc_name}</div></div>
      <div class="info-item"><div class="info-label">格式</div><div class="info-value">${data.format.toUpperCase()}</div></div>
      <div class="info-item"><div class="info-label">领域</div><div class="info-value">${data.domains.join(', ')}</div></div>
      <div class="info-item"><div class="info-label">文本块</div><div class="info-value">${data.filtered_chunks} / ${data.total_chunks}</div></div>
    </div>
    ${(cc||st)?`<div class="summary-section">${cc?`<div class="summary-title">核心组件</div><div class="summary-tags">${cc}</div>`:''}\
${st?`<div class="summary-title" style="margin-top:8px">可提取 Skill 类型</div><div class="summary-tags">${st}</div>`:''}</div>`:''}
    <div class="info-grid" style="margin-top:0">
      <div class="info-item"><div class="info-label">文档类型 <span style="color:#7c3aed">可调整 ▾</span></div>
        <select id="sel-book-type" class="setting-select">${typeOpts}</select></div>
      <div class="info-item"><div class="info-label">提取策略 <span style="color:#7c3aed">可调整 ▾</span></div>
        <select id="sel-prompt-type" class="setting-select">${promptOpts}</select></div>
    </div>`;

  document.getElementById('sel-book-type').addEventListener('change', function() {
    const m = {'技术手册':'extractor','叙事类':'narrative_extractor','方法论':'methodology_extractor','学术教材':'academic_extractor','操作规范':'extractor'};
    document.getElementById('sel-prompt-type').value = m[this.value]||'extractor'; saveSettings();
  });
  document.getElementById('sel-prompt-type').addEventListener('change', saveSettings);
  document.getElementById('phase1').classList.remove('active'); document.getElementById('phase1').classList.add('done');
  document.getElementById('phase1').classList.add('collapsed');
  document.getElementById('reupload-wrap').style.display = 'block';
  document.getElementById('phase2').classList.remove('hidden'); document.getElementById('phase2').classList.add('active');

  // 预填 baseline hint
  if (data.baseline_hint) {
    document.getElementById('prompt-hint').value = data.baseline_hint;
  }
  // 展示 system prompt
  if (data.system_prompt) {
    document.getElementById('system-prompt-display').textContent = data.system_prompt;
  }

  loadChunkSelector();
}

async function saveSettings() {
  if (!sessionId) return;
  await fetch('/api/session/'+sessionId+'/settings', {
    method:'PUT', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ book_type: document.getElementById('sel-book-type')?.value||'', prompt_type: document.getElementById('sel-prompt-type')?.value||'' })
  });
}

// ── 阶段 2：深度调优 ──
let _searchTimer = null;
async function loadChunkSelector(q) {
  try {
    const params = q ? `?q=${encodeURIComponent(q)}` : '?recommend=true';
    const r = await fetch('/api/chunks/'+sessionId+params);
    const data = await r.json();
    const sel = document.getElementById('chunk-select');
    document.getElementById('chunk-total').textContent = `共 ${data.total} 块`;
    sel.innerHTML = data.items.map(c =>
      `<option value="${c.index}">[${c.index}] ${c.heading_path.join(' > ')||'(无标题)'} — ${c.preview}</option>`
    ).join('');
  } catch(e) {}
}
function searchChunks() {
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(() => {
    const q = document.getElementById('chunk-search').value.trim();
    loadChunkSelector(q || undefined);
  }, 300);
}

async function runTune() {
  const idx = parseInt(document.getElementById('chunk-select').value);
  const hint = document.getElementById('prompt-hint').value.trim();
  document.getElementById('tune-loading').style.display = 'flex';
  document.getElementById('tune-result').style.display = 'none';
  try {
    const r = await fetch('/api/tune/'+sessionId, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ chunk_index: idx, prompt_hint: hint })
    });
    showTuneResult(await r.json());
    loadTuneHistory();
  } catch(e) { alert('调优失败: '+e.message); }
  document.getElementById('tune-loading').style.display = 'none';
}

function showTuneResult(d) {
  const el = document.getElementById('tune-result');
  el.style.display = 'block';
  const skills = d.extracted_skills.map(s => `
    <div class="skill-card" style="border-left:3px solid ${s.status==='failed'?'#ef4444':'#22c55e'}">
      <div class="skill-name">${s.name||'(unnamed)'}</div>
      <div class="skill-trigger">${s.trigger||''}</div>
      <span class="skill-domain">${s.domain||'general'}</span>
      <span style="font-size:11px;color:${s.status==='failed'?'#f87171':'#4ade80'}">${s.status}</span>
      <div class="skill-body">${s.body||''}</div>
    </div>`).join('');
  el.innerHTML = `<div class="tune-panel">
    <div class="source-pane">
      <div class="pane-title"><span class="dot source"></span> 原文 · chunk #${d.chunk_index}</div>
      <div style="font-size:11px;color:#52525b;margin-bottom:8px">${d.source_context}</div>
      <div class="source-text">${esc(d.source_text)}</div>
    </div>
    <div class="result-pane">
      <div class="pane-title"><span class="dot result"></span> 提取结果 · v${d.version} (${d.passed}✅ ${d.failed}❌)</div>
      ${d.prompt_hint_used?`<div style="font-size:11px;color:#7c3aed;margin-bottom:8px">📝 ${esc(d.prompt_hint_used)}</div>`:''}
      ${skills||'<div style="color:#71717a">EMPTY_BLOCK — 无可提取内容</div>'}
    </div>
  </div>`;
}

async function loadTuneHistory() {
  try {
    const r = await fetch('/api/tune-history/'+sessionId);
    const h = await r.json();
    if (!h.length) return;
    window._th = h;
    const wrap = document.getElementById('version-timeline-wrap');
    wrap.style.display = 'block';
    document.getElementById('version-timeline').innerHTML = h.map(v =>
      `<div class="version-chip" onclick="replayV(${v.version-1})" title="${v.timestamp}">v${v.version} · #${v.chunk_index}</div>`
    ).join('');
  } catch(e) {}
}

function replayV(i) {
  const v = window._th?.[i]; if (!v) return;
  document.getElementById('prompt-hint').value = v.prompt_hint||'';
  document.getElementById('chunk-select').value = v.chunk_index;
  const skills = (v.extracted_skills||[]).map(s => `
    <div class="skill-card" style="border-left:3px solid ${s.status==='failed'?'#ef4444':'#22c55e'}">
      <div class="skill-name">${s.name||'(unnamed)'}</div>
      <div class="skill-trigger">${s.trigger||''}</div>
      <span class="skill-domain">${s.domain||'general'}</span>
      <div class="skill-body">${s.body||''}</div>
    </div>`).join('');
  const el = document.getElementById('tune-result');
  el.style.display = 'block';
  el.innerHTML = `<div class="tune-panel">
    <div class="source-pane"><div class="pane-title"><span class="dot source"></span> 原文快照 #${v.chunk_index}</div>
      <div class="source-text">${esc(v.source_text_preview||'')}</div></div>
    <div class="result-pane"><div class="pane-title"><span class="dot result"></span> v${v.version} 历史回放</div>
      ${v.prompt_hint?`<div style="font-size:11px;color:#7c3aed;margin-bottom:8px">📝 ${esc(v.prompt_hint)}</div>`:''}
      ${skills||'<div style="color:#71717a">无结果</div>'}</div>
  </div>`;
  document.querySelectorAll('.version-chip').forEach((c,j) => c.classList.toggle('active', j===i));
}

// ── 阶段 3：抽样验证 ──
function goToSampleCheck() {
  document.getElementById('phase2').classList.remove('active'); document.getElementById('phase2').classList.add('done');
  document.getElementById('phase3').classList.remove('hidden'); document.getElementById('phase3').classList.add('active');
}

async function runSampleCheck() {
  document.getElementById('sample-loading').style.display = 'flex';
  document.getElementById('sample-result').style.display = 'none';
  try {
    const r = await fetch('/api/sample-check/'+sessionId, {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({sample_size:5})
    });
    showSampleResult(await r.json());
  } catch(e) { alert('抽样失败: '+e.message); }
  document.getElementById('sample-loading').style.display = 'none';
}

function showSampleResult(d) {
  const el = document.getElementById('sample-result');
  el.style.display = 'block';
  const items = d.results.map(r => {
    const hp = r.skills.some(s=>s.status==='pass');
    const tags = r.skills.map(s =>
      `<span class="sample-skill-tag ${s.status==='failed'?'fail':''}">${s.name||'?'} ${s.status==='pass'?'✅':'❌'}</span>`).join('');
    return `<div class="sample-item ${hp?'pass':'fail'}">
      <div class="sample-heading">#${r.chunk_index} · ${r.heading_path.join(' > ')||'(无标题)'}</div>
      <div class="sample-preview">${esc(r.source_preview)}</div>
      <div class="sample-skills">${tags||'<span style="color:#71717a">EMPTY</span>'}</div>
    </div>`;
  }).join('');
  el.innerHTML = `
    <div class="info-grid">
      <div class="info-item"><div class="info-label">抽样数</div><div class="info-value">${d.sample_size}</div></div>
      <div class="info-item"><div class="info-label">提取到</div><div class="info-value">${d.total_raw} Raw</div></div>
      <div class="info-item"><div class="info-label">通过率</div><div class="info-value highlight">${d.pass_rate}%</div></div>
      <div class="info-item"><div class="info-label">Hint</div><div class="info-value" style="font-size:12px">${d.prompt_hint_used||'(无)'}</div></div>
    </div>
    ${items}
    <div style="margin-top:16px;display:flex;gap:12px">
      <button class="btn btn-primary" onclick="goToExecute()">✅ 通过，开始全量</button>
      <button class="btn btn-ghost" onclick="runSampleCheck()">🔄 再抽一批</button>
      <button class="btn btn-ghost" onclick="backToTune()">↩ 返回调优</button>
    </div>`;
}

function backToTune() {
  document.getElementById('phase3').classList.remove('active'); document.getElementById('phase3').classList.add('hidden');
  document.getElementById('phase2').classList.remove('done'); document.getElementById('phase2').classList.add('active');
}

// ── 阶段 4：全量执行 ──
function goToExecute() {
  document.getElementById('phase2').classList.remove('active'); document.getElementById('phase2').classList.add('done');
  document.getElementById('phase3').classList.remove('active'); document.getElementById('phase3').classList.add('done');
  document.getElementById('phase4').classList.remove('hidden'); document.getElementById('phase4').classList.add('active');
  startExecute();
}

function startExecute() {
  const p = document.getElementById('execute-progress');
  p.style.display = 'block';
  p.innerHTML = `<div class="progress-bar"><div class="progress-fill" id="pbar"></div></div><div class="progress-text" id="ptext">准备中...</div>`;
  const src = new EventSource('/api/execute/'+sessionId);
  src.addEventListener('phase', e => {
    const d = JSON.parse(e.data);
    document.getElementById('ptext').textContent = d.message;
    if (d.done && d.total) document.getElementById('pbar').style.width = (d.done/d.total*100)+'%';
  });
  src.addEventListener('progress', e => {
    const d = JSON.parse(e.data);
    const pct = (d.completed/d.total*100).toFixed(0);
    document.getElementById('pbar').style.width = pct+'%';
    const eta = d.eta_s>60?(d.eta_s/60).toFixed(0)+'m':d.eta_s.toFixed(0)+'s';
    document.getElementById('ptext').textContent = `${d.completed}/${d.total} (${pct}%) | 💾 ${d.skills_on_disk||0} Skills | ⏱${d.elapsed_s.toFixed(0)}s ETA ${eta}`;
  });
  src.addEventListener('complete', e => {
    src.close();
    const d = JSON.parse(e.data);
    document.getElementById('pbar').style.width = '100%';
    const r = document.getElementById('execute-result');
    r.style.display = 'block';
    const skills = (d.skills||[]).map(s => `<div class="skill-card"><div class="skill-name">${s.name}</div><div class="skill-trigger">${s.trigger}</div><span class="skill-domain">${s.domain}</span><div class="skill-body">${s.body}</div></div>`).join('');
    r.innerHTML = `<div class="info-grid" style="margin-top:16px">
      <div class="info-item"><div class="info-label">最终 Skill</div><div class="info-value highlight">${d.final_skills}</div></div>
      <div class="info-item"><div class="info-label">耗时</div><div class="info-value">${d.elapsed_s}s</div></div>
      <div class="info-item"><div class="info-label">输出目录</div><div class="info-value">${d.output_dir}</div></div>
    </div><h3 style="margin:16px 0 8px;font-size:15px;color:#a1a1aa">最终 Skill 列表</h3>${skills}`;
    document.getElementById('phase4').classList.remove('active'); document.getElementById('phase4').classList.add('done');
    document.getElementById('ptext').textContent = `✅ 完成！${d.final_skills} Skills → ${d.output_dir}`;
  });
  src.onerror = () => { src.close(); document.getElementById('ptext').textContent = '❌ 连接中断'; };
}

function esc(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ── 页面恢复 ──
(async function() {
  if (!sessionId) return;
  try {
    const r = await fetch('/api/session/'+sessionId+'/state');
    if (!r.ok) { localStorage.removeItem('pdf2skill_session'); return; }
    const st = await r.json();
    const m = st.meta;
    document.getElementById('upload-area').style.display = 'none';
    const el = document.getElementById('analysis-result');
    el.style.display = 'block';
    const cc = (m.core_components||[]).map(c=>`<span class="summary-tag">${c}</span>`).join('');
    const stags = (m.skill_types||[]).map(c=>`<span class="summary-tag green">${c}</span>`).join('');
    el.innerHTML = `<div class="info-grid">
      <div class="info-item"><div class="info-label">文档名称</div><div class="info-value">${m.doc_name}</div></div>
      <div class="info-item"><div class="info-label">格式</div><div class="info-value">${m.format.toUpperCase()}</div></div>
      <div class="info-item"><div class="info-label">领域</div><div class="info-value">${(m.domains||[]).join(', ')}</div></div>
      <div class="info-item"><div class="info-label">文本块</div><div class="info-value">${m.filtered_chunks} / ${m.total_chunks}</div></div>
    </div>
    ${(cc||stags)?`<div class="summary-section">${cc?`<div class="summary-title">核心组件</div><div class="summary-tags">${cc}</div>`:''}\
${stags?`<div class="summary-title" style="margin-top:8px">可提取 Skill 类型</div><div class="summary-tags">${stags}</div>`:''}</div>`:''}
    <div class="info-grid" style="margin-top:0">
      <div class="info-item"><div class="info-label">文档类型</div><div class="info-value highlight">${m.book_type}</div></div>
      <div class="info-item"><div class="info-label">提取策略</div><div class="info-value highlight">${m.prompt_type}</div></div>
    </div>`;
    document.getElementById('phase1').classList.remove('active'); document.getElementById('phase1').classList.add('done');
    document.getElementById('phase1').classList.add('collapsed');
    document.getElementById('reupload-wrap').style.display = 'block';
    document.getElementById('phase2').classList.remove('hidden'); document.getElementById('phase2').classList.add('active');
    loadChunkSelector();
    loadTuneHistory();

    // 加载 prompt preview（baseline hint + system prompt）
    try {
      const pr = await fetch('/api/prompt-preview/'+sessionId);
      if (pr.ok) {
        const pp = await pr.json();
        if (pp.baseline_hint && !document.getElementById('prompt-hint').value) {
          document.getElementById('prompt-hint').value = pp.baseline_hint;
        }
        document.getElementById('system-prompt-display').textContent = pp.system_prompt || '';
      }
    } catch(e) {}
  } catch(e) { localStorage.removeItem('pdf2skill_session'); }
})();
</script>
</body>
</html>"""
