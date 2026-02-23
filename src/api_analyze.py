"""阶段一：文档上传 / 分析 / 设置 / 重切 / Prompt 预览"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File

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

_UPLOAD_DIR = Path("uploads")
_UPLOAD_DIR.mkdir(exist_ok=True)

# 内存缓存（schema 对象）
_schema_cache: dict[str, SkillSchema] = {}


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


@router.post("/analyze")
async def analyze_document(file: UploadFile = File(...)):
    """上传文档 → 类型检测 → Schema 生成 → 返回分析结果。"""
    notebook_id = str(uuid.uuid4())[:8]
    original_name = file.filename or "doc"
    upload_dir = _UPLOAD_DIR / notebook_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_path = upload_dir / original_name
    saved_path.write_bytes(await file.read())

    load_result = load_document(str(saved_path))
    chunk_result = chunk_markdown(
        load_result.markdown, load_result.doc_name,
        split_level=config.chunk.split_level,
    )
    filter_result = filter_chunks(chunk_result.chunks)

    schema = generate_schema(load_result.markdown, load_result.doc_name, client=DeepSeekClient())
    prompt_name, _ = _resolve_prompt_type(schema.book_type)

    nb = FileNotebook(notebook_id)
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
    (nb.root / "raw.md").write_text(load_result.markdown, encoding="utf-8")
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
        "baseline_hint": generate_baseline_hint(schema.book_type),
        "system_prompt": get_system_prompt_preview(schema.book_type, schema.to_prompt_constraint()),
    }


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
    nb.update_meta(meta)

    # 同步 schema
    if body.book_type is not None:
        schema_data = nb.load_schema() or {}
        schema_data["book_type"] = body.book_type
        nb._write_json("schema.json", schema_data)
        if notebook_id in _schema_cache:
            _schema_cache[notebook_id].book_type = body.book_type

    return {"ok": True, "book_type": meta.get("book_type"), "prompt_type": meta.get("prompt_type")}


@router.post("/rechunk/{notebook_id}")
async def rechunk_document(nb: NotebookDep, body: RechunkRequest):
    """重新切片。"""
    meta = nb.load_meta()
    md_path = nb.root / "raw.md"
    if not md_path.exists():
        from fastapi import HTTPException
        raise HTTPException(400, "原始文档不存在，请重新上传")

    raw_md = md_path.read_text(encoding="utf-8")
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
