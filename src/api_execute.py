"""阶段三：SSE 全量执行 + 笔记本列表 + 状态查询"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from .llm_client import AsyncDeepSeekClient
from .skill_extractor import extract_skills_batch
from .skill_validator import SkillValidator
from .notebook_store import FileNotebook, list_notebooks
from .callbacks import StatusCallback, EventType, create_logging_callback
from .api_analyze import get_schema

router = APIRouter(prefix="/api", tags=["execute"])


@router.get("/execute/{notebook_id}")
async def execute_full(request: Request, notebook_id: str):
    """SSE 全量执行（断点续传）。"""
    nb = FileNotebook(notebook_id)
    meta = nb.load_meta()
    if not meta:
        return JSONResponse({"error": "笔记本不存在"}, status_code=404)

    async def event_generator():
        event_queue: asyncio.Queue = asyncio.Queue()
        callback = StatusCallback()
        callback.add_callback(create_logging_callback("execute"))

        async def sse_callback(event_type: EventType, data: dict) -> None:
            sse_event_map = {
                EventType.PHASE_START: "phase",
                EventType.PHASE_END: "phase",
                EventType.CHUNK_PROGRESS: "progress",
                EventType.BATCH_COMPLETE: "batch_start",
                EventType.SKILL_VALIDATED: "skill_validated",
                EventType.SKILL_MERGED: "validation",
                EventType.INFO: "complete",
                EventType.ERROR: "error",
            }
            sse_name = sse_event_map.get(event_type, event_type.value)
            await event_queue.put({
                "event": sse_name,
                "data": json.dumps(data, ensure_ascii=False),
            })

        callback.add_callback(sse_callback)

        async def pipeline_task():
            schema = get_schema(notebook_id, nb)
            prompt_hint = nb.get_active_prompt_hint()
            total = nb.chunk_count()
            skill_idx = nb.skill_count()

            pending = nb.get_pending_chunk_indices(total)
            done_count = total - len(pending)

            if done_count > 0:
                await callback.emit(EventType.PHASE_START, {
                    "phase": "resume",
                    "message": f"📂 读档：已完成 {done_count}/{total}，从断点继续剩余 {len(pending)} 块",
                    "total": total, "done": done_count,
                })
            else:
                await callback.emit(EventType.PHASE_START, {
                    "phase": "extraction",
                    "message": f"开始全量提取：{total} 个文本块",
                    "total": total,
                })

            if not pending:
                all_skills_data = nb.load_skills()
                await callback.emit(EventType.INFO, {
                    "final_skills": len(all_skills_data),
                    "output_dir": f"notebooks/{notebook_id}/skills/",
                    "skills": [
                        {"name": s.get("name", ""), "trigger": s.get("trigger", ""),
                         "domain": s.get("domain", ""), "body": s.get("body", "")[:300]}
                        for s in all_skills_data[:30]
                    ],
                    "elapsed_s": 0, "resumed": True,
                })
                return

            async_client = AsyncDeepSeekClient()
            raw_count, completed = 0, done_count
            t_start = time.monotonic()
            batch_size = 5

            for batch_offset in range(0, len(pending), batch_size):
                if await request.is_disconnected():
                    nb.save_status(
                        phase="paused", completed=completed, total=total,
                        raw_skills=raw_count, passed=skill_idx,
                        elapsed_s=time.monotonic() - t_start,
                    )
                    return

                batch_indices = pending[batch_offset:batch_offset + batch_size]
                batch_chunks = nb.load_chunks_by_indices(batch_indices)

                await callback.emit(EventType.BATCH_COMPLETE, {
                    "batch_indices": batch_indices, "batch_size": len(batch_chunks),
                    "message": f"📦 开始处理批次 {batch_offset // batch_size + 1}（chunk {batch_indices[0]}-{batch_indices[-1]}）",
                })

                batch_skills = await extract_skills_batch(
                    batch_chunks, schema, client=async_client, prompt_hint=prompt_hint,
                )
                raw_count += len(batch_skills)
                completed += len(batch_chunks)

                passed_count, failed_count = 0, 0
                if batch_skills:
                    validator = SkillValidator()
                    source_map = {c.index: c.content for c in batch_chunks}
                    src_texts = [source_map.get(rs.source_chunk_index) for rs in batch_skills]
                    passed_batch, failed_batch = validator.validate_batch(batch_skills, source_texts=src_texts)
                    passed_count, failed_count = len(passed_batch), len(failed_batch)
                    for s in passed_batch:
                        nb.save_skill(s, idx=skill_idx)
                        skill_idx += 1
                        await callback.emit(EventType.SKILL_VALIDATED, {
                            "name": s.name, "domain": s.domain, "trigger": s.trigger[:80],
                        })

                await callback.emit(EventType.SKILL_MERGED, {
                    "batch_raw": len(batch_skills), "batch_passed": passed_count, "batch_failed": failed_count,
                    "message": f"✅ 批次完成：{len(batch_skills)} 提取 → {passed_count} 通过 / {failed_count} 失败",
                })

                nb.mark_chunks_done([c.index for c in batch_chunks])

                elapsed = time.monotonic() - t_start
                pending_left = total - completed
                eta = (pending_left / (completed - done_count) * elapsed) if completed > done_count else 0

                nb.save_status(
                    phase="extracting", completed=completed, total=total,
                    raw_skills=raw_count, passed=skill_idx, elapsed_s=elapsed,
                )

                await callback.emit(EventType.CHUNK_PROGRESS, {
                    "completed": completed, "total": total,
                    "raw_skills": raw_count, "skills_on_disk": skill_idx,
                    "elapsed_s": round(elapsed, 1), "eta_s": round(eta, 1),
                    "latest_skills": [
                        {"name": s.raw_text[:100], "source": s.source_context}
                        for s in batch_skills[:3]
                    ],
                })

            elapsed_total = time.monotonic() - t_start
            nb.save_status(
                phase="complete", completed=total, total=total,
                raw_skills=raw_count, passed=skill_idx,
                final_skills=skill_idx, elapsed_s=elapsed_total,
            )

            all_skills_data = nb.load_skills()
            sku_stats: dict[str, int] = {}
            for s in all_skills_data:
                st = s.get("sku_type", "procedural")
                sku_stats[st] = sku_stats.get(st, 0) + 1

            await callback.emit(EventType.INFO, {
                "final_skills": len(all_skills_data),
                "output_dir": f"notebooks/{notebook_id}/skills/",
                "sku_stats": sku_stats,
                "skills": [
                    {"name": s.get("name", ""), "trigger": s.get("trigger", ""),
                     "domain": s.get("domain", ""), "sku_type": s.get("sku_type", "procedural"),
                     "body": s.get("body", "")[:300]}
                    for s in all_skills_data[:30]
                ],
                "elapsed_s": round(elapsed_total, 1),
            })

        task = asyncio.create_task(pipeline_task())
        sentinel = object()

        def _on_done(_):
            event_queue.put_nowait(sentinel)

        task.add_done_callback(_on_done)

        while True:
            item = await event_queue.get()
            if item is sentinel:
                break
            yield item

        if task.done() and task.exception():
            yield {"event": "error", "data": json.dumps({"message": str(task.exception())})}

    return EventSourceResponse(event_generator())


@router.get("/sessions")
async def api_list_notebooks():
    """列出所有笔记本（兼容旧 /api/sessions 路径）。"""
    return list_notebooks()


@router.get("/notebooks")
async def api_list_notebooks_v2():
    """列出所有笔记本。"""
    return list_notebooks()


@router.get("/session/{notebook_id}/state")
async def api_notebook_state(notebook_id: str):
    """笔记本完整状态（页面刷新恢复 UI）。"""
    nb = FileNotebook(notebook_id)
    meta = nb.load_meta()
    if not meta:
        return JSONResponse({"error": "笔记本不存在"}, status_code=404)

    status = nb.load_status() or {}
    skills = nb.load_skills()
    total = nb.chunk_count()
    done = nb.get_done_count()

    return {
        "session_id": notebook_id,
        "notebook_id": notebook_id,
        "meta": meta,
        "status": status,
        "total_chunks": total,
        "done_chunks": done,
        "pending_chunks": total - done,
        "skills_on_disk": len(skills),
        "skills_preview": [
            {"name": s.get("name", ""), "trigger": s.get("trigger", ""),
             "domain": s.get("domain", ""), "body": s.get("body", "")[:500],
             "source_context": s.get("source_context", "")}
            for s in skills[:10]
        ],
    }
