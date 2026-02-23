"""阶段一：文档上传 / 分析 / 设置 / 重切 / Prompt 预览

上传流程：
1. POST /api/upload（批量）— 接收多文件，立即返回任务 ID，后台队列依次处理
2. GET /api/upload/progress/{notebook_id}（SSE）— 实时推送每个文件的处理进度
3. POST /api/analyze（单文件同步）— 向后兼容，阻塞直到处理完成

批量处理队列（asyncio.Queue）：
  去重(SHA-256) → 保存 upload/ → 文本化 → LLM 格式整理 → 合并 chunks → Schema
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, UploadFile, File
from fastapi.responses import StreamingResponse

from .config import config
from .deps import NotebookDep
from .schemas import SettingsUpdate, RechunkRequest
from .document_loader import load_document
from .llm_client import DeepSeekClient
from .markdown_chunker import chunk_markdown
from .schema_generator import SkillSchema, generate_schema
from .semantic_filter import filter_chunks
from .skill_extractor import (
    _resolve_prompt_type, generate_baseline_hint, get_system_prompt_preview,
)
from .notebook_store import FileNotebook

router = APIRouter(prefix="/api", tags=["analyze"])
_log = logging.getLogger(__name__)

# 内存缓存
_schema_cache: dict[str, SkillSchema] = {}

# ── 处理队列 ──
# 每个 notebook 一个队列，存储待处理文件
_upload_queues: dict[str, asyncio.Queue] = {}
# 每个 notebook 的文件处理状态 {notebook_id: {filename: {status, message, ...}}}
_upload_status: dict[str, dict[str, dict]] = {}
# 活跃的 worker task
_worker_tasks: dict[str, asyncio.Task] = {}

# ── LLM 文本整理 Prompt ──
_TEXT_CLEANUP_SYSTEM = """你是一个专业的文档格式整理助手。你的任务是对 OCR 或 PDF 提取的原始文本做格式整理。

严格规则：
1. **除了错别字，什么都别改。** 不修改语义、不删减内容、不添加内容、不改写句子。
2. 只修正明显的 OCR 错误和错别字。
3. 整理 Markdown 版式：正确的标题层级、段落分隔、列表缩进、表格对齐。
4. 删除无意义的乱码、重复页眉页脚、页码。
5. 合并被分页打断的段落。
6. 保留所有原始术语和专业名词，不做简繁转换。

输出：整理后的完整 Markdown 文本（直接输出内容，不要加额外说明）。"""


def get_schema(notebook_id: str, nb: FileNotebook) -> SkillSchema:
    """获取 Schema（优先缓存，否则磁盘重建）。"""
    if notebook_id in _schema_cache:
        return _schema_cache[notebook_id]
    schema_data = nb.load_schema()
    if not schema_data:
        raise ValueError(f"笔记本 {notebook_id} 的 Schema 不存在")
    meta = nb.load_meta() or {}
    schema = SkillSchema(
        book_name=schema_data.get("book_name", meta.get("doc_name", "document")),
        book_type=schema_data["book_type"],
        domains=schema_data["domains"],
    )
    _schema_cache[notebook_id] = schema
    return schema


def _cleanup_text_sync(raw_md: str) -> str:
    """同步 LLM 文本整理（分段处理长文本）。"""
    client = DeepSeekClient()

    if len(raw_md) < 6000:
        return client.chat(
            system_prompt=_TEXT_CLEANUP_SYSTEM,
            user_prompt=raw_md,
            temperature=0.1,
        ).content

    # 长文本分段
    paragraphs = raw_md.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for p in paragraphs:
        if current_len + len(p) > 5000 and current:
            chunks.append("\n\n".join(current))
            current = [p]
            current_len = len(p)
        else:
            current.append(p)
            current_len += len(p)
    if current:
        chunks.append("\n\n".join(current))

    cleaned = []
    for chunk in chunks:
        resp = client.chat(
            system_prompt=_TEXT_CLEANUP_SYSTEM,
            user_prompt=chunk,
            temperature=0.1,
        )
        cleaned.append(resp.content)

    return "\n\n".join(cleaned)


def _update_file_status(notebook_id: str, filename: str, **kwargs) -> None:
    """更新单个文件的处理状态。"""
    if notebook_id not in _upload_status:
        _upload_status[notebook_id] = {}
    if filename not in _upload_status[notebook_id]:
        _upload_status[notebook_id][filename] = {}
    _upload_status[notebook_id][filename].update(kwargs)


def _process_single_file(nb: FileNotebook, filename: str, file_bytes: bytes) -> dict:
    """处理单个文件（CPU 密集 + LLM 调用）。返回处理结果摘要。"""
    notebook_id = nb.notebook_id

    # 去重
    file_hash = nb.file_hash(file_bytes)
    if nb.is_duplicate(file_hash):
        return {"status": "skipped", "reason": "duplicate", "filename": filename}

    # 保存源文件
    _update_file_status(notebook_id, filename, status="saving", message="保存文件")
    saved_path = nb.upload_dir / filename
    saved_path.write_bytes(file_bytes)
    nb.register_file(filename, file_hash)

    # 文本化
    _update_file_status(notebook_id, filename, status="extracting", message="提取文本")
    load_result = load_document(str(saved_path))
    nb.save_raw_text(load_result.markdown, source_name=filename)

    # LLM 格式整理
    _update_file_status(notebook_id, filename, status="cleaning", message="LLM 格式整理")
    try:
        clean_md = _cleanup_text_sync(load_result.markdown)
        nb.save_clean_text(clean_md, source_name=filename)
    except Exception as e:
        _log.warning(f"LLM 文本整理失败 [{filename}]: {e}")
        clean_md = load_result.markdown

    _update_file_status(notebook_id, filename, status="done", message="完成",
                        doc_name=load_result.doc_name,
                        format=load_result.format.value,
                        chars=len(clean_md))

    return {
        "status": "processed",
        "filename": filename,
        "doc_name": load_result.doc_name,
        "format": load_result.format.value,
        "chars": len(clean_md),
        "clean_md": clean_md,
    }


async def _queue_worker(notebook_id: str) -> None:
    """后台队列 worker — 依次处理每个文件，处理完后合并做 chunk + schema。"""
    queue = _upload_queues[notebook_id]
    nb = FileNotebook(notebook_id)
    all_results: list[dict] = []

    while True:
        try:
            item = queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        filename, file_bytes = item
        _update_file_status(notebook_id, filename, status="queued", message="排队中")

        # 在线程池中执行 CPU/IO 密集操作
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, _process_single_file, nb, filename, file_bytes
        )
        all_results.append(result)
        queue.task_done()

    # 所有文件处理完成 → 合并文本 → chunk + schema
    processed = [r for r in all_results if r["status"] == "processed"]
    if not processed:
        _update_file_status(notebook_id, "__overall__", status="done", message="无新文件需要处理")
        return

    _update_file_status(notebook_id, "__overall__", status="chunking", message="切分 + Schema 生成")

    # 合并所有文件的 clean 文本
    merged_md = "\n\n---\n\n".join(r["clean_md"] for r in processed)
    primary = processed[0]

    # chunk + filter + schema（在线程池执行）
    def _finalize():
        chunk_result = chunk_markdown(
            merged_md, primary["doc_name"],
            split_level=config.chunk.split_level,
        )
        filter_result = filter_chunks(chunk_result.chunks)
        schema = generate_schema(merged_md, primary["doc_name"], client=DeepSeekClient())
        return chunk_result, filter_result, schema

    loop = asyncio.get_event_loop()
    chunk_result, filter_result, schema = await loop.run_in_executor(None, _finalize)

    prompt_name, _ = _resolve_prompt_type(schema.book_type)

    nb.save_meta(
        doc_name=primary["doc_name"],
        format=primary["format"],
        filepath=str(nb.upload_dir),
        book_type=schema.book_type,
        domains=schema.domains,
        total_chunks=len(chunk_result.chunks),
        filtered_chunks=len(filter_result.kept),
        prompt_type=prompt_name,
        core_components=schema.fields.get("core_components", []),
        skill_types=schema.fields.get("skill_types", []),
    )
    nb.save_schema(schema)
    nb.save_chunks(filter_result.kept)
    nb.save_status(phase="analyzed", total=len(filter_result.kept))

    system_prompt = get_system_prompt_preview(schema.book_type, schema.to_prompt_constraint())
    nb.save_prompt("system_prompt", system_prompt)
    baseline_hint = generate_baseline_hint(schema.book_type)
    if baseline_hint:
        nb.save_prompt("extraction_hint", baseline_hint)
    _schema_cache[notebook_id] = schema

    _update_file_status(notebook_id, "__overall__", status="done", message="全部完成",
                        total_files=len(processed),
                        skipped=len(all_results) - len(processed),
                        total_chunks=len(chunk_result.chunks),
                        filtered_chunks=len(filter_result.kept),
                        book_type=schema.book_type,
                        domains=schema.domains)

    # 清理
    _worker_tasks.pop(notebook_id, None)


# ══════════════════════════════════════════════
# 批量上传端点
# ══════════════════════════════════════════════


@router.post("/upload")
async def batch_upload(files: List[UploadFile] = File(...)):
    """批量上传文件 → 立即返回 notebook_id + 文件列表 → 后台队列依次处理。"""
    notebook_id = str(uuid.uuid4())[:8]
    nb = FileNotebook(notebook_id)

    # 创建队列
    queue: asyncio.Queue = asyncio.Queue()
    _upload_queues[notebook_id] = queue
    _upload_status[notebook_id] = {}

    file_list = []
    for f in files:
        filename = f.filename or "doc"
        file_bytes = await f.read()
        await queue.put((filename, file_bytes))
        _update_file_status(notebook_id, filename, status="queued", message="排队中",
                            size=len(file_bytes))
        file_list.append({"filename": filename, "size": len(file_bytes)})

    # 启动后台 worker
    task = asyncio.create_task(_queue_worker(notebook_id))
    _worker_tasks[notebook_id] = task

    return {
        "notebook_id": notebook_id,
        "total_files": len(file_list),
        "files": file_list,
        "message": f"已提交 {len(file_list)} 个文件，后台处理中。",
    }


@router.get("/upload/progress/{notebook_id}")
async def upload_progress(notebook_id: str):
    """SSE — 实时推送文件处理进度。"""
    async def _stream():
        while True:
            status = _upload_status.get(notebook_id, {})
            yield f"data: {json.dumps(status, ensure_ascii=False)}\n\n"

            # 检查是否全部完成
            overall = status.get("__overall__", {})
            if overall.get("status") == "done":
                yield f"event: done\ndata: {json.dumps(overall, ensure_ascii=False)}\n\n"
                # 清理状态
                _upload_status.pop(notebook_id, None)
                _upload_queues.pop(notebook_id, None)
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ══════════════════════════════════════════════
# 单文件同步端点（向后兼容）
# ══════════════════════════════════════════════


@router.post("/analyze")
async def analyze_document(file: UploadFile = File(...)):
    """单文件上传 → 同步处理 → 返回分析结果。（向后兼容）"""
    notebook_id = str(uuid.uuid4())[:8]
    original_name = file.filename or "doc"
    file_bytes = await file.read()

    nb = FileNotebook(notebook_id)

    # 去重
    file_hash = nb.file_hash(file_bytes)
    if nb.is_duplicate(file_hash):
        return {
            "duplicate": True,
            "notebook_id": notebook_id,
            "message": f"文件 {original_name} 已存在（SHA-256 匹配），跳过重复上传。",
        }

    # 保存
    saved_path = nb.upload_dir / original_name
    saved_path.write_bytes(file_bytes)
    nb.register_file(original_name, file_hash)

    # 文本化
    load_result = load_document(str(saved_path))
    nb.save_raw_text(load_result.markdown, source_name=original_name)

    # LLM 格式整理
    try:
        loop = asyncio.get_event_loop()
        clean_md = await loop.run_in_executor(
            None, _cleanup_text_sync, load_result.markdown
        )
        nb.save_clean_text(clean_md, source_name=original_name)
    except Exception as e:
        _log.warning(f"LLM 文本整理失败: {e}，使用原始文本")
        clean_md = load_result.markdown

    # chunk + filter
    chunk_result = chunk_markdown(
        clean_md, load_result.doc_name,
        split_level=config.chunk.split_level,
    )
    filter_result = filter_chunks(chunk_result.chunks)

    # schema
    schema = generate_schema(clean_md, load_result.doc_name, client=DeepSeekClient())
    prompt_name, _ = _resolve_prompt_type(schema.book_type)

    # 持久化
    nb.save_meta(
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
    nb.save_schema(schema)
    nb.save_chunks(filter_result.kept)
    nb.save_status(phase="analyzed", total=len(filter_result.kept))

    system_prompt = get_system_prompt_preview(schema.book_type, schema.to_prompt_constraint())
    nb.save_prompt("system_prompt", system_prompt)
    baseline_hint = generate_baseline_hint(schema.book_type)
    if baseline_hint:
        nb.save_prompt("extraction_hint", baseline_hint)
    _schema_cache[notebook_id] = schema

    return {
        "notebook_id": notebook_id,
        "session_id": notebook_id,
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
        "baseline_hint": baseline_hint,
        "system_prompt": system_prompt,
    }


# ══════════════════════════════════════════════
# 设置 / 重切 / Prompt 预览
# ══════════════════════════════════════════════


@router.put("/session/{notebook_id}/settings")
async def update_settings(notebook_id: str, body: SettingsUpdate):
    """调整提取设置（文档类型、提取策略）。"""
    nb = FileNotebook(notebook_id)
    meta = nb.load_meta()
    if not meta:
        from fastapi import HTTPException
        raise HTTPException(404, "笔记本不存在")

    if body.book_type is not None:
        meta["book_type"] = body.book_type
    if body.prompt_type is not None:
        meta["prompt_type"] = body.prompt_type
    if body.system_prompt is not None:
        meta["custom_system_prompt"] = body.system_prompt
        nb.save_prompt("system_prompt", body.system_prompt)
    nb.update_meta(meta)

    if body.book_type is not None:
        schema_data = nb.load_schema() or {}
        schema_data["book_type"] = body.book_type
        nb._write_json("text/schema.json", schema_data)
        if notebook_id in _schema_cache:
            _schema_cache[notebook_id].book_type = body.book_type

    return {"ok": True, "book_type": meta.get("book_type"), "prompt_type": meta.get("prompt_type")}


@router.post("/rechunk/{notebook_id}")
async def rechunk_document(nb: NotebookDep, body: RechunkRequest):
    """重新切片（基于 clean text 优先，回退 raw text）。"""
    meta = nb.load_meta()
    raw_md = nb.load_clean_text() or nb.load_raw_text()
    if not raw_md:
        from fastapi import HTTPException
        raise HTTPException(400, "原始文档不存在，请重新上传")

    chunk_result = chunk_markdown(raw_md, meta.get("doc_name", "document"), max_chars=body.max_chars, min_chars=body.min_chars)
    filter_result = filter_chunks(chunk_result.chunks)

    nb.save_chunks(filter_result.kept)
    meta["total_chunks"] = len(chunk_result.chunks)
    meta["filtered_chunks"] = filter_result.kept_count
    meta["chunk_strategy"] = chunk_result.strategy
    nb.update_meta(meta)

    return {
        "ok": True,
        "total_chunks": len(chunk_result.chunks),
        "filtered_chunks": filter_result.kept_count,
        "strategy": chunk_result.strategy,
        "chunks": [
            {"index": c.index,
             "heading": " > ".join(c.heading_path) if c.heading_path else f"Chunk #{c.index}",
             "char_count": c.char_count, "preview": c.content[:120]}
            for c in filter_result.kept
        ],
    }


@router.get("/prompt-preview/{notebook_id}")
async def prompt_preview(nb: NotebookDep):
    """返回当前完整 system prompt + 基线 hint。"""
    meta = nb.load_meta()
    schema = get_schema(nb.notebook_id, nb)
    book_type = meta.get("book_type", "技术手册")
    constraint = schema.to_prompt_constraint() if schema else ""

    return {
        "system_prompt": get_system_prompt_preview(book_type, constraint),
        "baseline_hint": generate_baseline_hint(book_type),
        "book_type": book_type,
        "prompt_type": meta.get("prompt_type", ""),
    }
