"""阶段一：文档上传 / 处理 / 设置 / 重切 / Prompt 预览

上传与处理流程（全自动）：
1. POST /api/upload — 存文件 + 去重 + 写 _progress.json + 自动开始处理
2. GET /api/upload/progress/{id} — SSE 实时推送处理进度
3. GET /api/upload/{id}/files — 文件列表（含状态 + 已处理文本）
4. POST /api/reprocess/{id}/{filename} — 重新处理单个文件

进度文件 upload/_progress.json：
  { "file.pdf": {"status": "done", "chars": 12345, "updated_at": ...}, ... }
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse

from .config import config
from .deps import WorkflowDep
from .schemas import SettingsUpdate, RechunkRequest
from .document_loader import load_document
from .llm_client import DeepSeekClient
from .markdown_chunker import chunk_markdown
from .schema_generator import SkillSchema, generate_schema
from .semantic_filter import filter_chunks
from .skill_extractor import (
    _resolve_prompt_type, generate_baseline_hint, get_system_prompt_preview,
)
from .workflow_store import FileWorkflow, generate_workflow_id

router = APIRouter(prefix="/api", tags=["analyze"])
_log = logging.getLogger(__name__)

# 内存缓存
_schema_cache: dict[str, SkillSchema] = {}

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


def get_schema(workflow_id: str, nb: FileWorkflow) -> SkillSchema:
    """获取 Schema（优先缓存，否则磁盘重建）。"""
    if workflow_id in _schema_cache:
        return _schema_cache[workflow_id]
    schema_data = nb.load_schema()
    if not schema_data:
        raise ValueError(f"工作流 {workflow_id} 的 Schema 不存在")
    meta = nb.load_meta() or {}
    schema = SkillSchema(
        book_name=schema_data.get("book_name", meta.get("doc_name", "document")),
        book_type=schema_data["book_type"],
        domains=schema_data["domains"],
    )
    _schema_cache[workflow_id] = schema
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


# ══════════════════════════════════════════════
# 进度文件 — upload/_progress.json
# ══════════════════════════════════════════════

_PROGRESS_FILE = "_progress.json"


def _load_progress(nb: FileWorkflow) -> dict:
    """读取 upload/_progress.json"""
    path = nb.upload_dir / _PROGRESS_FILE
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_progress(nb: FileWorkflow, progress: dict) -> None:
    """写入 upload/_progress.json"""
    (nb.upload_dir / _PROGRESS_FILE).write_text(
        json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _set_file_progress(nb: FileWorkflow, filename: str, **kwargs) -> None:
    """更新单个文件的进度"""
    progress = _load_progress(nb)
    if filename not in progress:
        progress[filename] = {}
    progress[filename].update(kwargs, updated_at=time.time())
    _save_progress(nb, progress)


# ══════════════════════════════════════════════
# 单文件处理逻辑
# ══════════════════════════════════════════════


def _process_single_file(nb: FileWorkflow, filename: str) -> dict:
    """处理 upload/ 中的单个文件 → raw + clean text。返回处理结果。"""
    filepath = nb.upload_dir / filename

    # 文本提取
    _set_file_progress(nb, filename, status="extracting", message="提取文本")
    load_result = load_document(str(filepath))
    nb.save_raw_text(load_result.markdown, source_name=filename)

    # LLM 格式整理
    _set_file_progress(nb, filename, status="cleaning", message="LLM 格式整理")
    try:
        clean_md = _cleanup_text_sync(load_result.markdown)
        nb.save_clean_text(clean_md, source_name=filename)
    except Exception as e:
        _log.warning(f"LLM 文本整理失败 [{filename}]: {e}")
        clean_md = load_result.markdown

    _set_file_progress(nb, filename, status="done", message="完成",
                       doc_name=load_result.doc_name,
                       format=load_result.format.value,
                       chars=len(clean_md))

    return {
        "status": "done",
        "filename": filename,
        "doc_name": load_result.doc_name,
        "format": load_result.format.value,
        "chars": len(clean_md),
        "clean_md": clean_md,
    }


async def _auto_process_worker(workflow_id: str) -> None:
    """后台 worker — 依次处理 _progress.json 中 pending 的文件，处理完后合并 chunk + schema。"""
    nb = FileWorkflow(workflow_id)
    progress = _load_progress(nb)
    all_results: list[dict] = []

    # 找出所有 pending 的文件
    pending = [f for f, info in progress.items() if info.get("status") == "pending"]

    for filename in pending:
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, _process_single_file, nb, filename)
            all_results.append(result)
        except Exception as e:
            _log.error(f"处理文件失败 [{filename}]: {e}")
            _set_file_progress(nb, filename, status="error", message=str(e))

    # 所有文件处理完成 → 合并文本 → chunk + schema
    processed = [r for r in all_results if r["status"] == "done"]
    if not processed:
        _worker_tasks.pop(workflow_id, None)
        return

    _set_file_progress(nb, "__overall__", status="chunking", message="切分 + Schema 生成")

    merged_md = "\n\n---\n\n".join(r["clean_md"] for r in processed)
    primary = processed[0]

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

    # 构建全部文件列表
    files_info = [
        {"doc_name": r["doc_name"], "format": r["format"], "chars": r.get("chars", 0)}
        for r in processed
    ]

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
        files=files_info,
    )
    nb.save_schema(schema)
    nb.save_chunks(filter_result.kept)
    nb.save_status(phase="analyzed", total=len(filter_result.kept))

    system_prompt = get_system_prompt_preview(schema.book_type, schema.to_prompt_constraint())
    nb.save_prompt("system_prompt", system_prompt)
    baseline_hint = generate_baseline_hint(schema.book_type)
    if baseline_hint:
        nb.save_prompt("extraction_hint", baseline_hint)
    _schema_cache[workflow_id] = schema

    _set_file_progress(nb, "__overall__", status="done", message="全部完成",
                       total_files=len(processed),
                       total_chunks=len(chunk_result.chunks),
                       filtered_chunks=len(filter_result.kept))

    _worker_tasks.pop(workflow_id, None)


# ══════════════════════════════════════════════
# 工作流管理端点
# ══════════════════════════════════════════════


@router.post("/workflow/create")
async def create_workflow(name: str = ""):
    """创建一个新的命名工作流（不上传文件）。"""
    workflow_id = generate_workflow_id()
    wf = FileWorkflow(workflow_id)
    wf.save_meta(name=name or "未命名工作流")
    return {
        "workflow_id": workflow_id,
        "name": name or "未命名工作流",
        "message": f"工作流「{name or '未命名工作流'}」已创建。",
    }


@router.get("/workflows")
async def api_list_workflows():
    """列出所有工作流。"""
    from .workflow_store import list_workflows
    return list_workflows()


# ══════════════════════════════════════════════
# 上传端点（存文件 + 自动开始处理）
# ══════════════════════════════════════════════


@router.post("/upload/{workflow_id}")
async def batch_upload(
    workflow_id: str,
    files: List[UploadFile] = File(...),
):
    """批量上传文件 → 存盘 + 去重 + 写进度文件 + 自动开始处理。"""
    wf = FileWorkflow(workflow_id)

    progress = _load_progress(wf)
    saved = []
    skipped = []

    for f in files:
        filename = f.filename or "doc"
        file_bytes = await f.read()

        # 去重
        file_hash = wf.file_hash(file_bytes)
        if wf.is_duplicate(file_hash):
            skipped.append({"filename": filename, "reason": "duplicate"})
            continue

        # 存盘
        (wf.upload_dir / filename).write_bytes(file_bytes)
        wf.register_file(filename, file_hash)
        saved.append({"filename": filename, "size": len(file_bytes)})

        # 标记为 pending
        progress[filename] = {"status": "pending", "message": "等待处理", "updated_at": time.time()}

    _save_progress(wf, progress)

    # 自动启动后台处理
    if saved:
        # 取消可能正在运行的旧 worker
        old_task = _worker_tasks.get(workflow_id)
        if old_task and not old_task.done():
            old_task.cancel()
        task = asyncio.create_task(_auto_process_worker(workflow_id))
        _worker_tasks[workflow_id] = task

    return {
        "workflow_id": workflow_id,
        "saved": saved,
        "skipped": skipped,
        "total_saved": len(saved),
        "total_skipped": len(skipped),
        "message": f"已保存 {len(saved)} 个文件，开始处理。" if saved else "无新文件。",
    }


@router.get("/upload/progress/{workflow_id}")
async def upload_progress_sse(workflow_id: str):
    """SSE — 实时推送文件处理进度（从 _progress.json 读取）。"""
    nb = FileWorkflow(workflow_id)

    async def _stream():
        while True:
            progress = _load_progress(nb)
            yield f"data: {json.dumps(progress, ensure_ascii=False)}\n\n"

            overall = progress.get("__overall__", {})
            if overall.get("status") in ("done", "error"):
                yield f"event: done\ndata: {json.dumps(overall, ensure_ascii=False)}\n\n"
                break

            # 所有文件都处理完但没有 overall（无 pending）
            file_statuses = [v.get("status") for k, v in progress.items() if k != "__overall__"]
            if file_statuses and all(s in ("done", "error") for s in file_statuses):
                yield f"event: done\ndata: {json.dumps({'status': 'done', 'message': '全部完成'}, ensure_ascii=False)}\n\n"
                break

            await asyncio.sleep(1)

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/upload/{workflow_id}/files")
async def list_upload_files(workflow_id: str):
    """返回文件列表 + 各文件处理状态 + 已处理文本（如有）。"""
    nb = FileWorkflow(workflow_id)
    if not nb.root.exists():
        raise HTTPException(404, "工作流不存在")

    progress = _load_progress(nb)
    files = []
    for f in sorted(nb.upload_dir.iterdir()):
        if f.is_file() and not f.name.startswith("_") and not f.name.startswith("."):
            stem = Path(f.name).stem
            info = progress.get(f.name, {})

            # 尝试读取已处理的文本
            clean_path = nb.text_dir / f"{stem}.md"
            raw_path = nb.text_dir / f"{stem}.raw.md"
            clean_text = clean_path.read_text(encoding="utf-8") if clean_path.exists() else None
            raw_text = raw_path.read_text(encoding="utf-8") if raw_path.exists() else None

            files.append({
                "filename": f.name,
                "size": f.stat().st_size,
                "status": info.get("status", "pending"),
                "message": info.get("message", ""),
                "chars": info.get("chars", 0),
                "clean_text": clean_text,
                "raw_text": raw_text,
            })

    return {"workflow_id": workflow_id, "files": files, "total": len(files)}


@router.post("/reprocess/{workflow_id}/{filename}")
async def reprocess_file(workflow_id: str, filename: str):
    """重新处理单个文件。"""
    nb = FileWorkflow(workflow_id)
    filepath = nb.upload_dir / filename
    if not filepath.exists():
        raise HTTPException(404, f"文件不存在: {filename}")

    _set_file_progress(nb, filename, status="pending", message="等待重新处理")

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _process_single_file, nb, filename)

    return {
        "workflow_id": workflow_id,
        "filename": filename,
        "status": result["status"],
        "chars": result.get("chars", 0),
        "message": "重新处理完成",
    }


@router.post("/chunk/{workflow_id}/{filename}")
async def chunk_single_file(workflow_id: str, filename: str):
    """启动 LLM 语义分块后台任务（立即返回）。通过 SSE 查询进度。"""
    nb = FileWorkflow(workflow_id)
    stem = Path(filename).stem

    # 检查文本是否存在
    clean_path = nb.text_dir / f"{stem}.md"
    raw_path = nb.text_dir / f"{stem}.raw.md"
    if not clean_path.exists() and not raw_path.exists():
        raise HTTPException(404, f"文件未处理或文本为空: {filename}")

    task_key = f"chunk:{workflow_id}:{filename}"

    # 初始化进度
    _chunk_progress[task_key] = {"status": "queued", "message": "排队等待分块", "segments_total": 0, "segments_done": 0, "chunks": 0}

    # 入队（队列 + 消费者自动串行执行）
    await _chunk_queue.put((workflow_id, filename, task_key))
    _ensure_chunk_consumer()

    return {
        "workflow_id": workflow_id,
        "filename": filename,
        "status": "queued",
        "message": "分块任务已入队，通过 SSE 查看进度。",
    }


# ── 分块任务队列（asyncio.Queue，无需 Redis）──
_chunk_queue: asyncio.Queue = asyncio.Queue()
_chunk_progress: dict[str, dict] = {}
_chunk_consumer_task: asyncio.Task | None = None


def _ensure_chunk_consumer():
    """确保全局只有一个消费者 worker 在运行。"""
    global _chunk_consumer_task
    if _chunk_consumer_task is None or _chunk_consumer_task.done():
        _chunk_consumer_task = asyncio.create_task(_chunk_consumer_loop())


async def _chunk_consumer_loop():
    """单消费者循环 — 从队列逐个取任务串行执行。"""
    while True:
        try:
            workflow_id, filename, task_key = await asyncio.wait_for(_chunk_queue.get(), timeout=60)
        except asyncio.TimeoutError:
            # 60 秒无新任务，退出消费者（下次入队时重启）
            break
        await _chunk_worker(workflow_id, filename, task_key)
        _chunk_queue.task_done()


async def _chunk_worker(workflow_id: str, filename: str, task_key: str):
    """后台 LLM 分块 worker。"""
    nb = FileWorkflow(workflow_id)
    stem = Path(filename).stem

    clean_path = nb.text_dir / f"{stem}.md"
    raw_path = nb.text_dir / f"{stem}.raw.md"
    text = clean_path.read_text(encoding="utf-8") if clean_path.exists() else raw_path.read_text(encoding="utf-8")

    # 读取 prompt
    prompt_path = Path(__file__).parent.parent / "prompts" / "chunker_v0.1.md"
    system_prompt = prompt_path.read_text(encoding="utf-8")

    # 分段
    segments = _split_text_segments(text, max_chars=4000)
    _chunk_progress[task_key] = {
        "status": "chunking",
        "message": f"共 {len(segments)} 段，开始处理",
        "segments_total": len(segments),
        "segments_done": 0,
        "chunks": 0,
    }

    from .llm_client import AsyncDeepSeekClient
    client = AsyncDeepSeekClient()

    all_chunks = []
    try:
        for seg_idx, segment in enumerate(segments):
            user_prompt = f"请处理以下文字：\n\n{segment}"
            resp = await client.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.1,
                max_tokens=8192,
            )
            parsed = _parse_jsonl_response(resp.content)
            all_chunks.extend(parsed)

            _chunk_progress[task_key] = {
                "status": "chunking",
                "message": f"已完成 {seg_idx + 1}/{len(segments)} 段，累计 {len(all_chunks)} 个片段",
                "segments_total": len(segments),
                "segments_done": seg_idx + 1,
                "chunks": len(all_chunks),
            }

        # 保存到 chunk/ 目录
        jsonl_path = nb.chunk_dir / f"{stem}.jsonl"
        lines = [json.dumps(c, ensure_ascii=False) for c in all_chunks]
        jsonl_path.write_text("\n".join(lines), encoding="utf-8")

        _chunk_progress[task_key] = {
            "status": "done",
            "message": f"已分块 {len(all_chunks)} 个片段 → chunk/{stem}.jsonl",
            "segments_total": len(segments),
            "segments_done": len(segments),
            "chunks": len(all_chunks),
            "jsonl_path": f"chunk/{stem}.jsonl",
        }
    except Exception as e:
        _chunk_progress[task_key] = {
            "status": "error",
            "message": str(e),
            "segments_total": len(segments),
            "segments_done": _chunk_progress.get(task_key, {}).get("segments_done", 0),
            "chunks": len(all_chunks),
        }


@router.get("/chunk/progress/{workflow_id}/{filename}")
async def chunk_progress_sse(workflow_id: str, filename: str):
    """SSE — 实时推送分块进度。"""
    task_key = f"chunk:{workflow_id}:{filename}"

    async def _stream():
        while True:
            progress = _chunk_progress.get(task_key, {"status": "unknown", "message": "无分块任务"})
            yield f"data: {json.dumps(progress, ensure_ascii=False)}\n\n"

            if progress.get("status") in ("done", "error"):
                yield f"event: done\ndata: {json.dumps(progress, ensure_ascii=False)}\n\n"
                break

            await asyncio.sleep(1)

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _split_text_segments(text: str, max_chars: int = 4000) -> list[str]:
    """按段落边界将长文本切为多个片段。保证不在句子中间断开。"""
    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split("\n\n")
    segments = []
    current = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para) + 2  # +2 for \n\n
        if current_len + para_len > max_chars and current:
            segments.append("\n\n".join(current))
            current = [para]
            current_len = para_len
        else:
            current.append(para)
            current_len += para_len

    if current:
        segments.append("\n\n".join(current))

    return segments


def _parse_jsonl_response(content: str) -> list[dict]:
    """从 LLM 响应中解析 JSONL 行。容忍 markdown 代码块包裹。"""
    # 去除可能的 ```jsonl ... ``` 包裹
    content = content.strip()
    if content.startswith("```"):
        # 去掉第一行 (```jsonl) 和最后一行 (```)
        lines = content.split("\n")
        content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    results = []
    for line in content.strip().split("\n"):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            results.append(obj)
        except json.JSONDecodeError:
            continue
    return results


# ══════════════════════════════════════════════
# 单文件同步端点（向后兼容）
# ══════════════════════════════════════════════


@router.post("/analyze")
async def analyze_document(file: UploadFile = File(...)):
    """单文件上传 → 同步处理 → 返回分析结果。（向后兼容）"""
    workflow_id = generate_workflow_id()
    original_name = file.filename or "doc"
    file_bytes = await file.read()

    nb = FileWorkflow(workflow_id)

    # 去重
    file_hash = nb.file_hash(file_bytes)
    if nb.is_duplicate(file_hash):
        return {
            "duplicate": True,
            "workflow_id": workflow_id,
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
    _schema_cache[workflow_id] = schema

    return {
        "workflow_id": workflow_id,
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


@router.put("/workflow/{workflow_id}/settings")
async def update_settings(workflow_id: str, body: SettingsUpdate):
    """调整提取设置（文档类型、提取策略）。"""
    nb = FileWorkflow(workflow_id)
    meta = nb.load_meta()
    if not meta:
        raise HTTPException(404, "工作流不存在")

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
        if workflow_id in _schema_cache:
            _schema_cache[workflow_id].book_type = body.book_type

    return {"ok": True, "book_type": meta.get("book_type"), "prompt_type": meta.get("prompt_type")}


@router.post("/rechunk/{workflow_id}")
async def rechunk_document(nb: WorkflowDep, body: RechunkRequest):
    """重新切片（基于 clean text 优先，回退 raw text）。"""
    meta = nb.load_meta()
    raw_md = nb.load_clean_text() or nb.load_raw_text()
    if not raw_md:
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


@router.get("/prompt-preview/{workflow_id}")
async def prompt_preview(nb: WorkflowDep):
    """返回当前完整 system prompt + 基线 hint。"""
    meta = nb.load_meta()
    schema = get_schema(nb.workflow_id, nb)
    book_type = meta.get("book_type", "技术手册")
    constraint = schema.to_prompt_constraint() if schema else ""

    return {
        "system_prompt": get_system_prompt_preview(book_type, constraint),
        "baseline_hint": generate_baseline_hint(book_type),
        "book_type": book_type,
        "prompt_type": meta.get("prompt_type", ""),
    }
