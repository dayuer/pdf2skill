"""阶段三：SSE 全量执行 + 笔记本列表 + 状态查询"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Request

from sse_starlette.sse import EventSourceResponse

from .deps import NotebookDep
from .llm_client import AsyncDeepSeekClient
from .skill_extractor import extract_skills_batch
from .skill_validator import SkillValidator
from .notebook_store import FileNotebook, list_notebooks
from .callbacks import StatusCallback, EventType, create_logging_callback
from .api_analyze import get_schema

router = APIRouter(prefix="/api", tags=["execute"])


@router.get("/execute/{notebook_id}")
async def execute_full(request: Request, nb: NotebookDep):
    """SSE 全量执行（断点续传）。"""

    async def event_generator():
        event_queue: asyncio.Queue = asyncio.Queue()
        callback = StatusCallback()
        callback.add_callback(create_logging_callback("execute"))

        async def sse_callback(event_type: EventType, data: dict) -> None:
            sse_map = {
                EventType.PHASE_START: "phase", EventType.PHASE_END: "phase",
                EventType.CHUNK_PROGRESS: "progress", EventType.BATCH_COMPLETE: "batch_start",
                EventType.SKILL_VALIDATED: "skill_validated", EventType.SKILL_MERGED: "validation",
                EventType.INFO: "complete", EventType.ERROR: "error",
            }
            await event_queue.put({
                "event": sse_map.get(event_type, event_type.value),
                "data": json.dumps(data, ensure_ascii=False),
            })

        callback.add_callback(sse_callback)

        async def pipeline_task():
            schema = get_schema(nb.notebook_id, nb)
            prompt_hint = nb.get_active_prompt_hint()
            total = nb.chunk_count()
            skill_idx = nb.skill_count()
            pending = nb.get_pending_chunk_indices(total)
            done_count = total - len(pending)

            if done_count > 0:
                await callback.emit(EventType.PHASE_START, {
                    "phase": "resume",
                    "message": f"📂 读档：已完成 {done_count}/{total}，继续剩余 {len(pending)} 块",
                    "total": total, "done": done_count,
                })
            else:
                await callback.emit(EventType.PHASE_START, {
                    "phase": "extraction",
                    "message": f"开始全量提取：{total} 个文本块", "total": total,
                })

            if not pending:
                skills = nb.load_skills()
                await callback.emit(EventType.INFO, {
                    "final_skills": len(skills),
                    "output_dir": f"notebooks/{nb.notebook_id}/skills/",
                    "skills": [_skill_summary(s) for s in skills[:30]],
                    "elapsed_s": 0, "resumed": True,
                })
                return

            async_client = AsyncDeepSeekClient()
            raw_count, completed = 0, done_count
            t_start = time.monotonic()

            for batch_offset in range(0, len(pending), 5):
                if await request.is_disconnected():
                    nb.save_status(
                        phase="paused", completed=completed, total=total,
                        raw_skills=raw_count, passed=skill_idx, elapsed_s=time.monotonic() - t_start,
                    )
                    return

                batch_indices = pending[batch_offset:batch_offset + 5]
                batch_chunks = nb.load_chunks_by_indices(batch_indices)

                await callback.emit(EventType.BATCH_COMPLETE, {
                    "batch_indices": batch_indices, "batch_size": len(batch_chunks),
                    "message": f"📦 批次 {batch_offset // 5 + 1}（chunk {batch_indices[0]}-{batch_indices[-1]}）",
                })

                batch_skills = await extract_skills_batch(
                    batch_chunks, schema, client=async_client, prompt_hint=prompt_hint,
                )
                raw_count += len(batch_skills)
                completed += len(batch_chunks)

                passed_n, failed_n = 0, 0
                if batch_skills:
                    validator = SkillValidator()
                    source_map = {c.index: c.content for c in batch_chunks}
                    src_texts = [source_map.get(rs.source_chunk_index) for rs in batch_skills]
                    passed_batch, failed_batch = validator.validate_batch(batch_skills, source_texts=src_texts)
                    passed_n, failed_n = len(passed_batch), len(failed_batch)
                    for s in passed_batch:
                        nb.save_skill(s, idx=skill_idx)
                        skill_idx += 1
                        await callback.emit(EventType.SKILL_VALIDATED, {
                            "name": s.name, "domain": s.domain, "trigger": s.trigger[:80],
                        })

                await callback.emit(EventType.SKILL_MERGED, {
                    "batch_raw": len(batch_skills), "batch_passed": passed_n, "batch_failed": failed_n,
                    "message": f"✅ {len(batch_skills)} 提取 → {passed_n} 通过 / {failed_n} 失败",
                })

                nb.mark_chunks_done([c.index for c in batch_chunks])
                elapsed = time.monotonic() - t_start
                eta = (total - completed) / max(completed - done_count, 1) * elapsed

                nb.save_status(
                    phase="extracting", completed=completed, total=total,
                    raw_skills=raw_count, passed=skill_idx, elapsed_s=elapsed,
                )
                await callback.emit(EventType.CHUNK_PROGRESS, {
                    "completed": completed, "total": total,
                    "raw_skills": raw_count, "skills_on_disk": skill_idx,
                    "elapsed_s": round(elapsed, 1), "eta_s": round(eta, 1),
                })

            elapsed_total = time.monotonic() - t_start
            nb.save_status(
                phase="complete", completed=total, total=total,
                raw_skills=raw_count, passed=skill_idx,
                final_skills=skill_idx, elapsed_s=elapsed_total,
            )

            all_skills = nb.load_skills()
            sku_stats: dict[str, int] = {}
            for s in all_skills:
                st = s.get("sku_type", "procedural")
                sku_stats[st] = sku_stats.get(st, 0) + 1

            await callback.emit(EventType.INFO, {
                "final_skills": len(all_skills),
                "output_dir": f"notebooks/{nb.notebook_id}/skills/",
                "sku_stats": sku_stats,
                "skills": [_skill_summary(s) for s in all_skills[:30]],
                "elapsed_s": round(elapsed_total, 1),
            })

        task = asyncio.create_task(pipeline_task())
        sentinel = object()
        task.add_done_callback(lambda _: event_queue.put_nowait(sentinel))

        while True:
            item = await event_queue.get()
            if item is sentinel:
                break
            yield item

        if task.done() and task.exception():
            yield {"event": "error", "data": json.dumps({"message": str(task.exception())})}

    return EventSourceResponse(event_generator())


def _skill_summary(s: dict) -> dict:
    """Skill 字典 → 摘要。"""
    return {
        "name": s.get("name", ""), "trigger": s.get("trigger", ""),
        "domain": s.get("domain", ""), "body": s.get("body", "")[:300],
    }


@router.get("/sessions")
@router.get("/notebooks")
async def api_list_notebooks():
    """列出所有笔记本。"""
    return list_notebooks()


@router.get("/session/{notebook_id}/state")
async def api_notebook_state(nb: NotebookDep):
    """笔记本完整状态（页面恢复 UI）。"""
    meta = nb.load_meta()
    skills = nb.load_skills()
    total = nb.chunk_count()
    done = nb.get_done_count()

    return {
        "notebook_id": nb.notebook_id,
        "session_id": nb.notebook_id,
        "meta": meta,
        "status": nb.load_status() or {},
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
