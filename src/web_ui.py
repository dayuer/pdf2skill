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
from .skill_extractor import extract_skills_batch, _resolve_prompt_type
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
                batch_chunks, schema, client=async_client
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
    background: #0a0a0f;
    color: #e4e4e7;
    min-height: 100vh;
  }
  .container { max-width: 960px; margin: 0 auto; padding: 40px 24px; }

  h1 {
    font-size: 32px; font-weight: 700;
    background: linear-gradient(135deg, #7c3aed, #06b6d4);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
  }
  .subtitle { color: #71717a; font-size: 14px; margin-bottom: 32px; }

  /* 阶段卡片 */
  .phase-card {
    background: #18181b; border: 1px solid #27272a;
    border-radius: 16px; padding: 24px; margin-bottom: 20px;
    transition: all 0.3s;
  }
  .phase-card.active { border-color: #7c3aed; box-shadow: 0 0 20px rgba(124,58,237,0.15); }
  .phase-card.done { border-color: #22c55e; opacity: 0.85; }
  .phase-card.hidden { display: none; }

  .phase-header {
    display: flex; align-items: center; gap: 12px; margin-bottom: 16px;
  }
  .phase-number {
    width: 32px; height: 32px; border-radius: 50%;
    background: #27272a; color: #71717a;
    display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 14px;
  }
  .phase-card.active .phase-number { background: #7c3aed; color: #fff; }
  .phase-card.done .phase-number { background: #22c55e; color: #fff; }
  .phase-title { font-size: 18px; font-weight: 600; }

  /* 上传区域 */
  .upload-zone {
    border: 2px dashed #3f3f46; border-radius: 12px;
    padding: 40px; text-align: center; cursor: pointer;
    transition: all 0.3s;
  }
  .upload-zone:hover { border-color: #7c3aed; background: rgba(124,58,237,0.05); }
  .upload-zone.dragover { border-color: #7c3aed; background: rgba(124,58,237,0.1); }
  .upload-icon { font-size: 48px; margin-bottom: 12px; }
  .upload-text { color: #a1a1aa; font-size: 14px; }
  input[type=file] { display: none; }

  /* 结果展示 */
  .info-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 12px; margin: 16px 0;
  }
  .info-item {
    background: #1f1f23; border-radius: 8px; padding: 12px;
  }
  .info-label { color: #71717a; font-size: 12px; margin-bottom: 4px; }
  .info-value { font-size: 16px; font-weight: 600; }
  .info-value.highlight { color: #7c3aed; }

  /* Skill 卡片 */
  .skill-card {
    background: #1f1f23; border: 1px solid #27272a;
    border-radius: 12px; padding: 16px; margin: 8px 0;
    transition: all 0.2s;
  }
  .skill-card:hover { border-color: #3f3f46; }
  .skill-name { font-weight: 600; color: #c084fc; margin-bottom: 4px; }
  .skill-trigger { color: #a1a1aa; font-size: 13px; margin-bottom: 8px; }
  .skill-domain {
    display: inline-block; background: rgba(124,58,237,0.2);
    color: #c084fc; padding: 2px 8px; border-radius: 4px;
    font-size: 11px; margin-right: 6px;
  }
  .skill-body {
    font-size: 13px; color: #a1a1aa; margin-top: 8px;
    white-space: pre-wrap; line-height: 1.6;
    max-height: 200px; overflow-y: auto;
  }

  /* 按钮 */
  .btn {
    padding: 10px 24px; border-radius: 8px; border: none;
    font-size: 14px; font-weight: 600; cursor: pointer;
    transition: all 0.2s;
  }
  .btn-primary {
    background: linear-gradient(135deg, #7c3aed, #6d28d9);
    color: #fff;
  }
  .btn-primary:hover { opacity: 0.9; transform: translateY(-1px); }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
  .btn-ghost {
    background: transparent; border: 1px solid #3f3f46; color: #a1a1aa;
  }
  .btn-ghost:hover { border-color: #7c3aed; color: #c084fc; }

  /* 进度条 */
  .progress-bar {
    width: 100%; height: 6px; background: #27272a;
    border-radius: 3px; overflow: hidden; margin: 12px 0;
  }
  .progress-fill {
    height: 100%; border-radius: 3px;
    background: linear-gradient(90deg, #7c3aed, #06b6d4);
    transition: width 0.5s ease;
    width: 0%;
  }
  .progress-text { font-size: 13px; color: #71717a; }

  /* 加载动画 */
  .spinner {
    display: inline-block; width: 20px; height: 20px;
    border: 2px solid #3f3f46; border-top-color: #7c3aed;
    border-radius: 50%; animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .loading-text { display: flex; align-items: center; gap: 10px; color: #a1a1aa; }

  /* 设置下拉框 */
  .setting-select {
    width: 100%; padding: 8px 10px; margin-top: 4px;
    background: #27272a; color: #e4e4e7; border: 1px solid #3f3f46;
    border-radius: 6px; font-size: 14px; font-weight: 600;
    cursor: pointer; outline: none;
  }
  .setting-select:focus { border-color: #7c3aed; }
  .setting-select option { background: #18181b; color: #e4e4e7; }
</style>
</head>
<body>
<div class="container">
  <h1>pdf2skill</h1>
  <p class="subtitle">智能文档解析 → 结构化知识提取</p>

  <!-- 阶段一：上传分析 -->
  <div id="phase1" class="phase-card active">
    <div class="phase-header">
      <div class="phase-number">1</div>
      <div class="phase-title">上传文档 · 类型检测</div>
    </div>
    <div id="upload-area">
      <div class="upload-zone" id="dropzone" onclick="document.getElementById('fileInput').click()">
        <div class="upload-icon">📄</div>
        <div class="upload-text">拖拽文件到此处，或点击选择<br>支持 PDF / TXT / EPUB</div>
      </div>
      <input type="file" id="fileInput" accept=".pdf,.txt,.epub,.md">
    </div>
    <div id="analysis-loading" style="display:none" class="loading-text">
      <div class="spinner"></div>
      <span>R1 正在分析文档类型和知识结构...</span>
    </div>
    <div id="analysis-result" style="display:none"></div>
  </div>

  <!-- 阶段二：采样预览 -->
  <div id="phase2" class="phase-card hidden">
    <div class="phase-header">
      <div class="phase-number">2</div>
      <div class="phase-title">采样预览 · 确认方向</div>
    </div>
    <div id="preview-loading" style="display:none" class="loading-text">
      <div class="spinner"></div>
      <span>R1 正在采样提取 5 个文本块...</span>
    </div>
    <div id="preview-result" style="display:none"></div>
  </div>

  <!-- 阶段三：全量执行 -->
  <div id="phase3" class="phase-card hidden">
    <div class="phase-header">
      <div class="phase-number">3</div>
      <div class="phase-title">全量执行 · 实时结果</div>
    </div>
    <div id="execute-progress" style="display:none"></div>
    <div id="execute-result" style="display:none"></div>
    <div id="skill-stream"></div>
  </div>
</div>

<script>
let sessionId = localStorage.getItem('pdf2skill_session');

// ── 页面加载：自动恢复状态 ──
(async function restoreState() {
  if (!sessionId) return;
  try {
    const res = await fetch('/api/session/' + sessionId + '/state');
    if (!res.ok) { localStorage.removeItem('pdf2skill_session'); return; }
    const state = await res.json();
    const phase = state.status?.phase || 'analyzed';
    const meta = state.meta;

    // 恢复阶段一
    document.getElementById('upload-area').style.display = 'none';
    const el = document.getElementById('analysis-result');
    el.style.display = 'block';
    el.innerHTML = `
      <div class="info-grid">
        <div class="info-item"><div class="info-label">文档名称</div><div class="info-value">${meta.doc_name}</div></div>
        <div class="info-item"><div class="info-label">文档类型</div><div class="info-value highlight">${meta.book_type}</div></div>
        <div class="info-item"><div class="info-label">格式</div><div class="info-value">${meta.format.toUpperCase()}</div></div>
        <div class="info-item"><div class="info-label">领域</div><div class="info-value">${(meta.domains||[]).join(', ')}</div></div>
        <div class="info-item"><div class="info-label">文本块</div><div class="info-value">${meta.filtered_chunks} / ${meta.total_chunks}</div></div>
        <div class="info-item"><div class="info-label">提取策略</div><div class="info-value highlight">${meta.prompt_type}</div></div>
      </div>
      <div style="margin-top:16px; display:flex; gap:12px;">
        <button class="btn btn-primary" onclick="startPreview()">📋 采样预览（5块）</button>
        <button class="btn btn-ghost" onclick="startExecute()">⚡ 跳过预览，直接全量</button>
      </div>
    `;
    document.getElementById('phase1').classList.remove('active');
    document.getElementById('phase1').classList.add('done');

    // 根据 phase 恢复到对应阶段
    if (phase === 'analyzed') {
      document.getElementById('phase2').classList.remove('hidden');
      document.getElementById('phase2').classList.add('active');
    } else if (phase === 'previewed' || phase === 'extracting' || phase === 'paused' || phase === 'complete') {
      document.getElementById('phase2').classList.remove('hidden');
      document.getElementById('phase2').classList.add('done');
      document.getElementById('phase3').classList.remove('hidden');
      document.getElementById('phase3').classList.add('active');

      // 显示已有 Skills
      if (state.skills_preview.length > 0) {
        const previewEl = document.getElementById('preview-result');
        previewEl.style.display = 'block';
        previewEl.innerHTML = state.skills_preview.map(s => `
          <div class="skill-card">
            <div class="skill-name">${s.name || '(unnamed)'}</div>
            <div class="skill-trigger">${s.trigger || ''}</div>
            <span class="skill-domain">${s.domain || 'general'}</span>
            <div class="skill-body">${s.body}</div>
          </div>
        `).join('');
      }

      if (phase === 'complete') {
        const progressEl = document.getElementById('execute-progress');
        progressEl.style.display = 'block';
        progressEl.innerHTML = `
          <div class="progress-bar"><div class="progress-fill" style="width:100%"></div></div>
          <div class="progress-text">✅ 已完成！💾 ${state.skills_on_disk} Skills</div>
        `;
        document.getElementById('phase3').classList.remove('active');
        document.getElementById('phase3').classList.add('done');
      } else {
        // 未完成 → 显示继续按钮
        const progressEl = document.getElementById('execute-progress');
        progressEl.style.display = 'block';
        const pct = state.total_chunks > 0 ? (state.done_chunks/state.total_chunks*100).toFixed(0) : 0;
        progressEl.innerHTML = `
          <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
          <div class="progress-text">📂 已完成 ${state.done_chunks}/${state.total_chunks} | 💾 ${state.skills_on_disk} Skills</div>
          <div style="margin-top:12px">
            <button class="btn btn-primary" onclick="startExecute()">▶️ 从断点继续（剩余 ${state.pending_chunks} 块）</button>
            <button class="btn btn-ghost" onclick="localStorage.removeItem('pdf2skill_session');location.reload()">🗑️ 放弃，重新开始</button>
          </div>
        `;
      }
    }
  } catch (e) {
    localStorage.removeItem('pdf2skill_session');
  }
})();

// 上传
const fileInput = document.getElementById('fileInput');
const dropzone = document.getElementById('dropzone');

dropzone.addEventListener('dragover', e => { e.preventDefault(); dropzone.classList.add('dragover'); });
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
dropzone.addEventListener('drop', e => {
  e.preventDefault(); dropzone.classList.remove('dragover');
  if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => { if (fileInput.files[0]) uploadFile(fileInput.files[0]); });

async function uploadFile(file) {
  document.getElementById('upload-area').style.display = 'none';
  document.getElementById('analysis-loading').style.display = 'flex';

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch('/api/analyze', { method: 'POST', body: formData });
    const data = await res.json();
    sessionId = data.session_id;
    localStorage.setItem('pdf2skill_session', sessionId);
    showAnalysis(data);
  } catch (err) {
    alert('分析失败: ' + err.message);
    document.getElementById('upload-area').style.display = 'block';
    document.getElementById('analysis-loading').style.display = 'none';
  }
}

function showAnalysis(data) {
  document.getElementById('analysis-loading').style.display = 'none';
  const el = document.getElementById('analysis-result');
  el.style.display = 'block';

  const typeOptions = ['技术手册', '叙事类', '方法论', '学术教材'].map(t =>
    `<option value="${t}" ${t === data.book_type ? 'selected' : ''}>${t}</option>`
  ).join('');

  const promptMap = {'技术手册':'extractor','叙事类':'narrative_extractor','方法论':'methodology_extractor','学术教材':'academic_extractor'};
  const promptOptions = Object.entries(promptMap).map(([label, val]) =>
    `<option value="${val}" ${val === data.prompt_type ? 'selected' : ''}>${val} (${label})</option>`
  ).join('');

  el.innerHTML = `
    <div class="info-grid">
      <div class="info-item"><div class="info-label">文档名称</div><div class="info-value">${data.doc_name}</div></div>
      <div class="info-item">
        <div class="info-label">文档类型 <span style="color:#7c3aed">可调整 ▾</span></div>
        <select id="sel-book-type" class="setting-select">${typeOptions}</select>
      </div>
      <div class="info-item"><div class="info-label">格式</div><div class="info-value">${data.format.toUpperCase()}</div></div>
      <div class="info-item"><div class="info-label">领域</div><div class="info-value">${data.domains.join(', ')}</div></div>
      <div class="info-item"><div class="info-label">文本块</div><div class="info-value">${data.filtered_chunks} / ${data.total_chunks}</div></div>
      <div class="info-item">
        <div class="info-label">提取策略 <span style="color:#7c3aed">可调整 ▾</span></div>
        <select id="sel-prompt-type" class="setting-select">${promptOptions}</select>
      </div>
    </div>
    <div style="margin-top:16px; display:flex; gap:12px;">
      <button class="btn btn-primary" onclick="startPreview()">📋 采样预览（5块）</button>
      <button class="btn btn-ghost" onclick="startExecute()">⚡ 跳过预览，直接全量</button>
    </div>
  `;

  // 联动：切换文档类型自动更新提取策略
  document.getElementById('sel-book-type').addEventListener('change', function() {
    const pm = {'技术手册':'extractor','叙事类':'narrative_extractor','方法论':'methodology_extractor','学术教材':'academic_extractor'};
    document.getElementById('sel-prompt-type').value = pm[this.value] || 'extractor';
    saveSettings();
  });
  document.getElementById('sel-prompt-type').addEventListener('change', saveSettings);

  document.getElementById('phase1').classList.remove('active');
  document.getElementById('phase1').classList.add('done');
  document.getElementById('phase2').classList.remove('hidden');
  document.getElementById('phase2').classList.add('active');
}

async function saveSettings() {
  if (!sessionId) return;
  await fetch('/api/session/' + sessionId + '/settings', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      book_type: document.getElementById('sel-book-type')?.value || '',
      prompt_type: document.getElementById('sel-prompt-type')?.value || '',
    }),
  });
}

async function startPreview() {
  document.getElementById('preview-loading').style.display = 'flex';
  document.getElementById('preview-result').style.display = 'none';
  try {
    const res = await fetch('/api/preview/' + sessionId, { method: 'POST' });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`服务端错误 (${res.status}): ${text.slice(0, 200)}`);
    }
    const data = await res.json();
    showPreview(data);
  } catch (err) {
    alert('预览失败: ' + err.message);
  }
  document.getElementById('preview-loading').style.display = 'none';
}

function showPreview(data) {
  const el = document.getElementById('preview-result');
  el.style.display = 'block';

  let skillsHtml = data.skills.map(s => `
    <div class="skill-card">
      <div class="skill-name">${s.name || '(unnamed)'}</div>
      <div class="skill-trigger">${s.trigger || ''}</div>
      <span class="skill-domain">${s.domain || 'general'}</span>
      <div style="font-size:11px; color:#52525b; margin-top:4px">${s.source_context || ''}</div>
      <div class="skill-body">${s.body}</div>
    </div>
  `).join('');

  el.innerHTML = `
    <div class="info-grid">
      <div class="info-item"><div class="info-label">采样块数</div><div class="info-value">${data.sample_chunks}</div></div>
      <div class="info-item"><div class="info-label">提取到</div><div class="info-value">${data.raw_skills} Raw</div></div>
      <div class="info-item"><div class="info-label">通过校验</div><div class="info-value highlight">${data.passed}</div></div>
      <div class="info-item"><div class="info-label">失败</div><div class="info-value">${data.failed}</div></div>
    </div>
    <h3 style="margin:16px 0 8px; font-size:15px; color:#a1a1aa;">样本 Skill 预览</h3>
    ${skillsHtml}
    <div style="margin-top:16px; display:flex; gap:12px;">
      <button class="btn btn-primary" onclick="startExecute()">✅ 确认方向，全量执行</button>
      <button class="btn btn-ghost" onclick="startPreview()">🔄 重新采样</button>
    </div>
  `;

  document.getElementById('phase2').classList.remove('active');
  document.getElementById('phase2').classList.add('done');
  document.getElementById('phase3').classList.remove('hidden');
  document.getElementById('phase3').classList.add('active');
}

function startExecute() {
  // 确保阶段三可见
  document.getElementById('phase2').classList.remove('active');
  document.getElementById('phase2').classList.add('done');
  document.getElementById('phase3').classList.remove('hidden');
  document.getElementById('phase3').classList.add('active');

  const progressEl = document.getElementById('execute-progress');
  progressEl.style.display = 'block';
  progressEl.innerHTML = `
    <div class="progress-bar"><div class="progress-fill" id="pbar"></div></div>
    <div class="progress-text" id="ptext">准备中...</div>
  `;

  const source = new EventSource('/api/execute/' + sessionId);

  source.addEventListener('phase', (e) => {
    const d = JSON.parse(e.data);
    document.getElementById('ptext').textContent = d.message;
    // 断点续传：设置初始进度条位置
    if (d.done && d.total) {
      document.getElementById('pbar').style.width = (d.done/d.total*100)+'%';
    }
  });

  source.addEventListener('progress', (e) => {
    const d = JSON.parse(e.data);
    const pct = (d.completed / d.total * 100).toFixed(0);
    document.getElementById('pbar').style.width = pct + '%';
    const eta = d.eta_s > 60 ? (d.eta_s/60).toFixed(0)+'m' : d.eta_s.toFixed(0)+'s';
    document.getElementById('ptext').textContent =
      `${d.completed}/${d.total} (${pct}%) | ` +
      `💾 ${d.skills_on_disk || 0} Skills | ⏱${d.elapsed_s.toFixed(0)}s ETA ${eta}`;

    // 流式展示最新 Skill
    const stream = document.getElementById('skill-stream');
    if (d.latest_skills) {
      d.latest_skills.forEach(s => {
        const card = document.createElement('div');
        card.className = 'skill-card';
        card.innerHTML = `<div class="skill-body">${s.name}</div>`;
        stream.prepend(card);
      });
    }
  });

  source.addEventListener('validation', (e) => {
    const d = JSON.parse(e.data);
    document.getElementById('ptext').textContent =
      `校验完成：✅${d.passed} ❌${d.failed}`;
  });

  source.addEventListener('complete', (e) => {
    source.close();
    const d = JSON.parse(e.data);
    document.getElementById('pbar').style.width = '100%';
    const resultEl = document.getElementById('execute-result');
    resultEl.style.display = 'block';

    let skillsHtml = d.skills.map(s => `
      <div class="skill-card">
        <div class="skill-name">${s.name || '(unnamed)'}</div>
        <div class="skill-trigger">${s.trigger || ''}</div>
        <span class="skill-domain">${s.domain || 'general'}</span>
        <div class="skill-body">${s.body}</div>
      </div>
    `).join('');

    resultEl.innerHTML = `
      <div class="info-grid" style="margin-top:16px">
        <div class="info-item"><div class="info-label">最终 Skill</div><div class="info-value highlight">${d.final_skills}</div></div>
        <div class="info-item"><div class="info-label">耗时</div><div class="info-value">${d.elapsed_s}s</div></div>
        <div class="info-item"><div class="info-label">输出目录</div><div class="info-value">${d.output_dir}</div></div>
      </div>
      <h3 style="margin:16px 0 8px; font-size:15px; color:#a1a1aa;">最终 Skill 列表</h3>
      ${skillsHtml}
    `;

    document.getElementById('phase3').classList.remove('active');
    document.getElementById('phase3').classList.add('done');
    document.getElementById('ptext').textContent =
      `✅ 完成！${d.final_skills} Skills → ${d.output_dir}`;
  });

  source.onerror = () => {
    source.close();
    document.getElementById('ptext').textContent = '❌ 连接中断';
  };
}
</script>
</body>
</html>"""
