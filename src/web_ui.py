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
        # 统计 SKU 分布
        sku_stats = {}
        for s in all_skills_data:
            st = s.get("sku_type", "procedural")
            sku_stats[st] = sku_stats.get(st, 0) + 1
        yield {
            "event": "complete",
            "data": json.dumps({
                "final_skills": len(all_skills_data),
                "output_dir": f"sessions/{session_id}/skills/",
                "sku_stats": sku_stats,
                "skills": [
                    {
                        "name": s.get("name", ""),
                        "trigger": s.get("trigger", ""),
                        "domain": s.get("domain", ""),
                        "sku_type": s.get("sku_type", "procedural"),
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
  /* ── 顶栏 ── */
  .topbar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 16px 24px; border-bottom: 1px solid #27272a;
    background: #111114;
  }
  .topbar h1 {
    font-size: 22px; font-weight: 700;
    background: linear-gradient(135deg, #7c3aed, #06b6d4);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }
  .topbar-info { display: flex; align-items: center; gap: 16px; font-size: 13px; color: #71717a; }
  .topbar-info .tag { padding: 2px 10px; border-radius: 6px; background: #27272a; color: #a1a1aa; font-size: 12px; }
  .topbar-info .tag.active { background: rgba(124,58,237,0.2); color: #c084fc; }

  /* ── 主体布局 ── */
  .main { display: flex; height: calc(100vh - 57px); }
  .panel { overflow-y: auto; padding: 20px; }
  .left { width: 42%; border-right: 1px solid #27272a; display: flex; flex-direction: column; }
  .right { flex: 1; display: flex; flex-direction: column; }

  /* ── 上传区 ── */
  .upload-zone {
    border: 2px dashed #3f3f46; border-radius: 12px; padding: 40px;
    text-align: center; cursor: pointer; transition: all 0.3s;
    margin: 20px;
  }
  .upload-zone:hover { border-color: #7c3aed; background: rgba(124,58,237,0.05); }
  .upload-icon { font-size: 40px; margin-bottom: 12px; }
  .upload-text { color: #71717a; font-size: 14px; }
  input[type=file] { display: none; }

  /* ── 文档摘要 ── */
  .doc-summary {
    padding: 12px 16px; background: #18181b; border-radius: 10px;
    margin: 0 0 12px; font-size: 13px; border: 1px solid #27272a;
  }
  .doc-summary .row { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 6px; }
  .doc-summary .label { color: #71717a; }
  .doc-summary .val { color: #e4e4e7; font-weight: 500; }
  .doc-summary .val.hl { color: #c084fc; }
  .summary-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
  .summary-tag { padding: 2px 8px; border-radius: 4px; font-size: 11px; background: rgba(124,58,237,0.12); color: #c084fc; }
  .summary-tag.green { background: rgba(34,197,94,0.12); color: #4ade80; }

  /* ── 设置行 ── */
  .settings-row { display: flex; gap: 12px; margin-bottom: 12px; }
  .setting-select {
    flex: 1; padding: 6px 10px; background: #27272a; color: #e4e4e7;
    border: 1px solid #3f3f46; border-radius: 8px; font-size: 13px;
  }

  /* ── Chunk 列表 ── */
  .chunk-search {
    width: 100%; padding: 8px 12px; background: #1f1f23; color: #e4e4e7;
    border: 1px solid #3f3f46; border-radius: 8px; font-size: 13px;
    margin-bottom: 8px;
  }
  .chunk-count { font-size: 12px; color: #52525b; margin-bottom: 8px; }
  .chunk-list { flex: 1; overflow-y: auto; }
  .chunk-item {
    padding: 10px 12px; margin-bottom: 4px; border-radius: 8px;
    background: #18181b; border: 1px solid transparent; cursor: pointer;
    font-size: 12px; color: #a1a1aa; transition: all 0.15s;
    line-height: 1.5;
  }
  .chunk-item:hover { border-color: #3f3f46; background: #1f1f23; }
  .chunk-item.selected { border-color: #7c3aed; background: rgba(124,58,237,0.08); color: #e4e4e7; }
  .chunk-item .idx { color: #7c3aed; font-weight: 600; margin-right: 6px; }
  .chunk-item .path { color: #71717a; font-size: 11px; display: block; margin-top: 2px; }

  /* ── 右栏 ── */
  .section { margin-bottom: 16px; }
  .section-title { font-size: 13px; color: #71717a; margin-bottom: 8px; display: flex; align-items: center; gap: 6px; }
  .prompt-display {
    background: #1f1f23; padding: 12px; border-radius: 8px;
    max-height: 200px; overflow-y: auto; font-size: 12px;
    color: #a1a1aa; white-space: pre-wrap; border: 1px solid #27272a;
  }
  .prompt-textarea {
    width: 100%; min-height: 100px; padding: 12px; background: #1f1f23;
    color: #e4e4e7; border: 1px solid #3f3f46; border-radius: 8px;
    font-size: 13px; resize: vertical; font-family: inherit; line-height: 1.6;
  }
  .prompt-textarea:focus { outline: none; border-color: #7c3aed; }

  /* ── 按钮 ── */
  .btn {
    padding: 8px 20px; border: none; border-radius: 8px; font-size: 13px;
    cursor: pointer; transition: all 0.2s; font-weight: 500;
  }
  .btn-primary { background: #7c3aed; color: #fff; }
  .btn-primary:hover { background: #6d28d9; }
  .btn-ghost { background: transparent; color: #a1a1aa; border: 1px solid #3f3f46; }
  .btn-ghost:hover { border-color: #7c3aed; color: #c084fc; }
  .btn-sm { padding: 5px 12px; font-size: 12px; }
  .btn-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 10px; }

  /* ── 结果卡片 ── */
  .result-pane {
    flex: 1; overflow-y: auto; background: #18181b;
    border-radius: 10px; padding: 16px; border: 1px solid #27272a;
  }
  .skill-card {
    padding: 12px; margin-bottom: 8px; border-radius: 8px;
    background: #1f1f23; border-left: 3px solid #22c55e;
  }
  .skill-card.fail { border-left-color: #ef4444; }
  .skill-name { font-weight: 600; font-size: 14px; color: #f4f4f5; margin-bottom: 4px; }
  .skill-trigger { font-size: 12px; color: #a1a1aa; margin-bottom: 6px; }
  .skill-domain { padding: 2px 8px; border-radius: 4px; font-size: 11px; background: rgba(124,58,237,0.12); color: #c084fc; }
  .skill-body { margin-top: 8px; font-size: 12px; color: #a1a1aa; white-space: pre-wrap; max-height: 120px; overflow-y: auto; }

  /* ── 原文预览 ── */
  .source-preview {
    background: #111114; padding: 12px; border-radius: 8px;
    font-size: 12px; color: #a1a1aa; white-space: pre-wrap;
    max-height: 200px; overflow-y: auto; margin-bottom: 12px;
    border: 1px solid #27272a; line-height: 1.6;
  }

  /* ── 版本时间线 ── */
  .version-timeline { display: flex; gap: 8px; flex-wrap: wrap; }
  .version-dot {
    padding: 4px 10px; border-radius: 12px; font-size: 11px;
    background: #27272a; color: #a1a1aa; cursor: pointer; transition: all 0.2s;
  }
  .version-dot:hover { background: rgba(124,58,237,0.2); color: #c084fc; }
  .version-dot.active { background: #7c3aed; color: #fff; }

  /* ── 进度条 ── */
  .progress-bar { height: 6px; background: #27272a; border-radius: 3px; overflow: hidden; }
  .progress-fill { height: 100%; background: linear-gradient(90deg, #7c3aed, #06b6d4); width: 0; transition: width 0.3s; }
  .progress-text { font-size: 12px; color: #71717a; margin-top: 6px; }

  /* ── 加载 ── */
  .spinner { width: 16px; height: 16px; border: 2px solid #3f3f46; border-top-color: #7c3aed; border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .loading-text { display: flex; align-items: center; gap: 8px; color: #71717a; font-size: 13px; padding: 12px; }

  /* ── 抽样验证 ── */
  .sample-card {
    padding: 10px; margin-bottom: 6px; border-radius: 8px;
    background: #1f1f23; border: 1px solid #27272a; font-size: 12px;
  }
  .sample-pass { color: #4ade80; } .sample-fail { color: #f87171; }
</style>
</head>
<body>

<!-- 顶栏 -->
<div class="topbar">
  <div style="display:flex;align-items:center;gap:16px">
    <h1>pdf2skill</h1>
    <span id="doc-name-display" style="font-size:13px;color:#71717a"></span>
  </div>
  <div class="topbar-info">
    <span id="strategy-tag" class="tag" style="display:none"></span>
    <span id="chunk-count-tag" class="tag" style="display:none"></span>
    <button class="btn btn-ghost btn-sm" onclick="resetSession()" style="display:none" id="btn-reupload">📄 重新上传</button>
  </div>
</div>

<div class="main">
  <!-- 左栏：上传 + 文档信息 + Chunk 列表 -->
  <div class="left panel" id="left-panel">
    <div id="upload-area">
      <div class="upload-zone" id="dropzone" onclick="document.getElementById('fileInput').click()">
        <div class="upload-icon">📄</div>
        <div class="upload-text">拖拽文件到此处，或点击选择<br>支持 PDF / TXT / EPUB</div>
      </div>
      <input type="file" id="fileInput" accept=".pdf,.txt,.epub,.md">
    </div>
    <div id="analysis-loading" style="display:none" class="loading-text"><div class="spinner"></div><span>R1 正在分析文档类型和知识结构...</span></div>
    <div id="doc-summary" style="display:none"></div>
    <div id="chunk-panel" style="display:none; flex-direction:column; min-height:0; flex:1">
      <input id="chunk-search" class="chunk-search" placeholder="搜索 chunk 内容…" oninput="searchChunks()">
      <div id="chunk-count" class="chunk-count"></div>
      <div id="chunk-list" class="chunk-list"></div>
    </div>
  </div>

  <!-- 右栏：Prompt 操作台 -->
  <div class="right panel" id="right-panel">
    <div id="right-placeholder" style="display:flex; align-items:center; justify-content:center; height:100%; color:#3f3f46; font-size:15px;">
      ← 上传文档后进入操作台
    </div>
    <div id="workspace" style="display:none; flex-direction:column; min-height:0; flex:1">
      <!-- 系统 Prompt -->
      <div class="section">
        <details>
          <summary class="section-title" style="cursor:pointer">🔍 系统 Prompt（点击展开）</summary>
          <div id="system-prompt-display" class="prompt-display"></div>
        </details>
      </div>

      <!-- Prompt 编辑器 -->
      <div class="section">
        <div class="section-title">✏️ 提取策略（系统已根据文档类型预填）</div>
        <textarea id="prompt-hint" class="prompt-textarea" placeholder="加载中..."></textarea>
        <div class="btn-row">
          <button class="btn btn-primary" onclick="runTune()">🔬 提取并对比</button>
          <button class="btn btn-ghost btn-sm" onclick="runSampleCheck()">🎲 抽样验证 (5块)</button>
          <button class="btn btn-ghost btn-sm" onclick="startExecute()">⚡ 全量执行</button>
        </div>
      </div>

      <!-- 加载状态 -->
      <div id="tune-loading" style="display:none" class="loading-text"><div class="spinner"></div><span>R1 正在提取...</span></div>

      <!-- 原文预览 -->
      <div id="source-preview-section" class="section" style="display:none">
        <div class="section-title">📖 原文 · chunk #<span id="source-chunk-idx"></span></div>
        <div id="source-preview" class="source-preview"></div>
      </div>

      <!-- 提取结果 -->
      <div id="result-section" class="section" style="display:none; flex:1; min-height:0">
        <div class="section-title">🎯 提取结果 <span id="result-stats" style="color:#52525b;font-size:11px"></span></div>
        <div id="result-cards" class="result-pane"></div>
      </div>

      <!-- 抽样验证结果 -->
      <div id="sample-section" class="section" style="display:none">
        <div class="section-title">🎲 抽样验证结果 <span id="sample-stats" style="font-size:11px"></span></div>
        <div id="sample-cards"></div>
      </div>

      <!-- 全量执行进度 -->
      <div id="execute-section" class="section" style="display:none">
        <div class="section-title">⚡ 全量执行</div>
        <div class="progress-bar"><div class="progress-fill" id="pbar"></div></div>
        <div class="progress-text" id="ptext">准备中...</div>
        <div id="execute-result" style="margin-top:12px"></div>
      </div>

      <!-- 版本历史 -->
      <div id="version-section" class="section" style="display:none">
        <div class="section-title">🕐 版本历史（点击回溯）</div>
        <div id="version-timeline" class="version-timeline"></div>
      </div>
    </div>
  </div>
</div>

<script>
let sessionId = localStorage.getItem('pdf2skill_session');
let selectedChunkIdx = null;

function resetSession() {
  localStorage.removeItem('pdf2skill_session');
  location.reload();
}

function esc(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ── 上传 ──
const fileInput = document.getElementById('fileInput');
const dropzone = document.getElementById('dropzone');
dropzone.addEventListener('dragover', e => { e.preventDefault(); dropzone.style.borderColor = '#7c3aed'; });
dropzone.addEventListener('dragleave', () => { dropzone.style.borderColor = '#3f3f46'; });
dropzone.addEventListener('drop', e => { e.preventDefault(); dropzone.style.borderColor = '#3f3f46'; if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]); });
fileInput.addEventListener('change', () => { if (fileInput.files[0]) uploadFile(fileInput.files[0]); });

async function uploadFile(file) {
  document.getElementById('upload-area').style.display = 'none';
  document.getElementById('analysis-loading').style.display = 'flex';

  const fd = new FormData(); fd.append('file', file);
  try {
    const r = await fetch('/api/analyze', { method: 'POST', body: fd });
    const data = await r.json();
    if (!r.ok) { alert(data.detail || '分析失败'); location.reload(); return; }
    sessionId = data.session_id;
    localStorage.setItem('pdf2skill_session', sessionId);
    showWorkspace(data);
  } catch(e) { alert('上传失败: ' + e.message); location.reload(); }
}

// ── 展示工作区 ──
function showWorkspace(data) {
  document.getElementById('analysis-loading').style.display = 'none';
  document.getElementById('upload-area').style.display = 'none';
  document.getElementById('right-placeholder').style.display = 'none';
  document.getElementById('workspace').style.display = 'flex';
  document.getElementById('btn-reupload').style.display = '';
  document.getElementById('doc-name-display').textContent = '《' + data.doc_name + '》';
  document.getElementById('strategy-tag').style.display = '';
  document.getElementById('strategy-tag').textContent = data.prompt_type;
  document.getElementById('chunk-count-tag').style.display = '';
  document.getElementById('chunk-count-tag').textContent = data.filtered_chunks + ' chunks';

  // 文档摘要
  const cc = (data.core_components||[]).map(c=>'<span class="summary-tag">'+c+'</span>').join('');
  const st = (data.skill_types||[]).map(c=>'<span class="summary-tag green">'+c+'</span>').join('');
  const typeOpts = ['技术手册','叙事类','方法论','学术教材','操作规范'].map(t =>
    '<option'+(t===data.book_type?' selected':'')+'>'+t+'</option>').join('');
  const ds = document.getElementById('doc-summary');
  ds.style.display = 'block';
  ds.innerHTML = '<div class="doc-summary">' +
    '<div class="row"><span class="label">格式</span><span class="val">' + data.format.toUpperCase() + '</span>' +
    '<span class="label">领域</span><span class="val">' + (data.domains||[]).join(', ') + '</span>' +
    '<span class="label">块数</span><span class="val">' + data.filtered_chunks + ' / ' + data.total_chunks + '</span></div>' +
    (cc||st ? '<div class="summary-tags" style="margin-bottom:6px">' + cc + st + '</div>' : '') +
    '<div class="settings-row">' +
      '<select id="sel-book-type" class="setting-select" onchange="autoPromptType();saveSettings()">' + typeOpts + '</select>' +
    '</div></div>';

  // Baseline hint + system prompt
  if (data.baseline_hint) document.getElementById('prompt-hint').value = data.baseline_hint;
  if (data.system_prompt) document.getElementById('system-prompt-display').textContent = data.system_prompt;

  // Chunk 列表
  document.getElementById('chunk-panel').style.display = 'flex';
  loadChunkList();
}

function autoPromptType() {
  const m = {'技术手册':'extractor','叙事类':'narrative_extractor','方法论':'methodology_extractor','学术教材':'academic_extractor','操作规范':'extractor'};
  // 保存时自动推导
}

async function saveSettings() {
  if (!sessionId) return;
  await fetch('/api/session/'+sessionId+'/settings', {
    method:'PUT', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ book_type: document.getElementById('sel-book-type')?.value||'' })
  });
}

// ── Chunk 列表 ──
let _searchTimer = null;
async function loadChunkList(q) {
  try {
    const params = q ? '?q='+encodeURIComponent(q)+'&page_size=50' : '?page_size=50';
    const r = await fetch('/api/chunks/'+sessionId+params);
    const data = await r.json();
    document.getElementById('chunk-count').textContent = '共 ' + data.total + ' 块' + (q ? '（筛选）' : '');
    const list = document.getElementById('chunk-list');
    list.innerHTML = data.items.map(c =>
      '<div class="chunk-item' + (c.index===selectedChunkIdx?' selected':'') + '" onclick="selectChunk('+c.index+')" data-idx="'+c.index+'">' +
        '<span class="idx">#'+c.index+'</span>' + esc(c.preview) +
        '<span class="path">' + (c.heading_path.join(' > ')||'') + '</span>' +
      '</div>'
    ).join('');
  } catch(e) {}
}

function searchChunks() {
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(() => {
    loadChunkList(document.getElementById('chunk-search').value.trim() || undefined);
  }, 300);
}

function selectChunk(idx) {
  selectedChunkIdx = idx;
  document.querySelectorAll('.chunk-item').forEach(el => {
    el.classList.toggle('selected', parseInt(el.dataset.idx) === idx);
  });
}

// ── 调优 ──
async function runTune() {
  if (selectedChunkIdx === null) { alert('请先在左栏选择一个 chunk'); return; }
  const hint = document.getElementById('prompt-hint').value.trim();
  document.getElementById('tune-loading').style.display = 'flex';
  document.getElementById('result-section').style.display = 'none';
  document.getElementById('source-preview-section').style.display = 'none';
  try {
    const r = await fetch('/api/tune/'+sessionId, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ chunk_index: selectedChunkIdx, prompt_hint: hint })
    });
    const d = await r.json();
    showTuneResult(d);
    loadTuneHistory();
  } catch(e) { alert('调优失败: '+e.message); }
  document.getElementById('tune-loading').style.display = 'none';
}

function showTuneResult(d) {
  // 原文预览
  document.getElementById('source-preview-section').style.display = 'block';
  document.getElementById('source-chunk-idx').textContent = d.chunk_index;
  document.getElementById('source-preview').textContent = d.source_text || '';

  // 提取结果
  const sec = document.getElementById('result-section');
  sec.style.display = 'flex';
  const skills = d.extracted_skills || [];
  const passed = skills.filter(s=>s.status!=='failed').length;
  document.getElementById('result-stats').textContent = 'v' + (d.version||'?') + ' · ' + passed + '✅ ' + (skills.length-passed) + '❌';
  document.getElementById('result-cards').innerHTML = skills.map(s =>
    '<div class="skill-card' + (s.status==='failed'?' fail':'') + '">' +
      '<div class="skill-name">' + esc(s.name||'(unnamed)') + '</div>' +
      '<div class="skill-trigger">' + esc(s.trigger||'') + '</div>' +
      '<span class="skill-domain">' + esc(s.domain||'general') + '</span>' +
      '<div class="skill-body">' + esc(s.body||'') + '</div>' +
    '</div>'
  ).join('') || '<div style="color:#52525b;padding:20px;text-align:center">EMPTY_BLOCK — 无可提取内容</div>';
}

// ── 版本历史 ──
async function loadTuneHistory() {
  try {
    const r = await fetch('/api/tune-history/'+sessionId);
    const history = await r.json();
    if (!history.length) return;
    document.getElementById('version-section').style.display = 'block';
    document.getElementById('version-timeline').innerHTML = history.map((h,i) =>
      '<div class="version-dot' + (i===history.length-1?' active':'') + '" onclick="replayVersion('+i+')" title="chunk#'+h.chunk_index+' '+h.timestamp+'">' +
        'v' + h.version + '</div>'
    ).join('');
    window._tuneHistory = history;
  } catch(e) {}
}

function replayVersion(idx) {
  const h = window._tuneHistory[idx];
  if (!h) return;
  document.getElementById('prompt-hint').value = h.prompt_hint || '';
  selectedChunkIdx = h.chunk_index;
  document.querySelectorAll('.chunk-item').forEach(el => {
    el.classList.toggle('selected', parseInt(el.dataset.idx) === h.chunk_index);
  });
  showTuneResult({
    chunk_index: h.chunk_index,
    source_text: h.source_text_preview || '',
    extracted_skills: h.extracted_skills || [],
    version: h.version,
  });
  document.querySelectorAll('.version-dot').forEach((el,i) => el.classList.toggle('active', i===idx));
}

// ── 抽样验证 ──
async function runSampleCheck() {
  document.getElementById('sample-section').style.display = 'block';
  document.getElementById('sample-cards').innerHTML = '<div class="loading-text"><div class="spinner"></div><span>R1 正在批量提取和校验...</span></div>';
  try {
    const r = await fetch('/api/sample-check/'+sessionId, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ sample_size: 5 })
    });
    const d = await r.json();
    const passRate = d.total > 0 ? (d.passed/d.total*100).toFixed(0) : 0;
    document.getElementById('sample-stats').innerHTML = '<span class="'+(passRate>=60?'sample-pass':'sample-fail')+'">通过率 '+passRate+'% ('+d.passed+'/'+d.total+')</span>';
    document.getElementById('sample-cards').innerHTML = (d.details||[]).map(item =>
      '<div class="sample-card">' +
        '<div style="display:flex;justify-content:space-between;margin-bottom:4px"><span>#'+item.chunk_index+'</span>' +
        '<span class="'+(item.valid?'sample-pass':'sample-fail')+'">'+(item.valid?'✅':'❌')+'</span></div>' +
        '<div style="color:#71717a;font-size:11px">' + esc((item.source_preview||'').substring(0,100)) + '</div>' +
        (item.skills||[]).map(s=>'<span class="summary-tag" style="margin-top:4px">'+esc(s)+'</span>').join('') +
      '</div>'
    ).join('');
  } catch(e) { document.getElementById('sample-cards').innerHTML = '<div style="color:#f87171">验证失败: '+e.message+'</div>'; }
}

// ── 全量执行 ──
function startExecute() {
  if (!confirm('开始全量执行？将使用当前 prompt 策略处理所有 chunk。')) return;
  const sec = document.getElementById('execute-section');
  sec.style.display = 'block';
  document.getElementById('pbar').style.width = '0';
  document.getElementById('ptext').textContent = '准备中...';
  document.getElementById('execute-result').innerHTML = '';

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
    document.getElementById('ptext').textContent = d.completed+'/'+d.total+' ('+pct+'%) | 💾 '+(d.skills_on_disk||0)+' Skills | ⏱'+d.elapsed_s.toFixed(0)+'s ETA '+eta;
  });
  src.addEventListener('complete', e => {
    src.close();
    const d = JSON.parse(e.data);
    document.getElementById('pbar').style.width = '100%';
    document.getElementById('ptext').textContent = '✅ 完成！'+d.final_skills+' Skills → '+d.output_dir;
    const skuInfo = d.sku_stats ? ' | 📋'+( d.sku_stats.factual||0)+' 事实 ⚙️'+(d.sku_stats.procedural||0)+' 程序 🔗'+(d.sku_stats.relational||0)+' 关系' : '';
    const typeColors = {factual:'#3b82f6',procedural:'#22c55e',relational:'#f59e0b'};
    const skills = (d.skills||[]).map(s =>
      '<div class="skill-card"><div class="skill-name">'+esc(s.name)+'</div><div class="skill-trigger">'+esc(s.trigger)+'</div><span class="skill-domain">'+esc(s.domain)+'</span> <span style="padding:2px 8px;border-radius:4px;font-size:11px;background:'+(typeColors[s.sku_type]||'#666')+'20;color:'+(typeColors[s.sku_type]||'#aaa')+'">'+esc(s.sku_type||'')+'</span><div class="skill-body">'+esc(s.body)+'</div></div>'
    ).join('');
    document.getElementById('execute-result').innerHTML =
      '<div style="margin-top:8px"><span class="val hl">'+d.final_skills+' SKUs</span> · '+d.elapsed_s+'s'+skuInfo+'</div>' + skills;
  });
  src.onerror = () => { src.close(); document.getElementById('ptext').textContent = '❌ 连接中断'; };
}

// ── 页面恢复 ──
(async function() {
  if (!sessionId) return;
  try {
    const r = await fetch('/api/session/'+sessionId+'/state');
    if (!r.ok) { localStorage.removeItem('pdf2skill_session'); return; }
    const st = await r.json();
    showWorkspace(st.meta);
    loadTuneHistory();
    // 加载 prompt preview
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

