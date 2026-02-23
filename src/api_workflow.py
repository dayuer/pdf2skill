"""工作流引擎 API — SSE 流式执行 / save / load / pin-data

对标 n8n 的执行反馈模式：通过 SSE 实时推送节点状态。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .deps import NotebookDep
from .schemas import WorkflowExecuteRequest, WorkflowSaveRequest
from .notebook_store import FileNotebook
from .workflow_engine import WorkflowEngine
from .workflow_types import WorkflowDefinition

router = APIRouter(prefix="/api/workflow", tags=["workflow"])

_engine = WorkflowEngine()


@router.post("/execute")
async def api_workflow_execute(body: WorkflowExecuteRequest):
    """SSE 流式执行工作流 — 实时推送每个节点的状态。"""
    notebook_id = body.notebook_id
    if not notebook_id:
        raise HTTPException(400, "需要 notebook_id")

    nb = FileNotebook(notebook_id)

    # 解析工作流定义
    definition = WorkflowDefinition.from_json(body.workflow)
    workflow = _engine.build(definition)

    # SSE 事件队列
    event_queue: asyncio.Queue = asyncio.Queue()

    def on_event(event_type: str, node_id: str, data: dict):
        event_queue.put_nowait({
            "event": event_type,
            "node_id": node_id,
            "data": data,
        })

    async def generate():
        # 启动执行（在后台任务中）
        task = asyncio.create_task(
            _engine.execute(
                workflow,
                context={"notebook": nb, "notebook_id": notebook_id},
                on_event=on_event,
            )
        )

        # 流式发送事件
        while not task.done() or not event_queue.empty():
            try:
                evt = await asyncio.wait_for(event_queue.get(), timeout=0.5)
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                # 心跳
                yield ": heartbeat\n\n"

        # 获取最终结果
        exec_ctx = task.result()
        summary = _engine.to_json(exec_ctx, workflow)
        yield f"data: {json.dumps({'event': 'workflow:result', 'data': summary}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/execute-sync")
async def api_workflow_execute_sync(body: WorkflowExecuteRequest):
    """同步执行工作流 — 返回完整结果（向后兼容）。"""
    notebook_id = body.notebook_id
    if not notebook_id:
        raise HTTPException(400, "需要 notebook_id")

    nb = FileNotebook(notebook_id)
    definition = WorkflowDefinition.from_json(body.workflow)
    workflow = _engine.build(definition)

    events: list[dict] = []

    def on_event(event_type: str, node_id: str, data: dict):
        events.append({"event": event_type, "node_id": node_id, "data": data})

    exec_ctx = await _engine.execute(
        workflow,
        context={"notebook": nb, "notebook_id": notebook_id},
        on_event=on_event,
    )

    return {
        "run_id": exec_ctx.run_id,
        "status": exec_ctx.status.value,
        "elapsed_s": exec_ctx.elapsed_s,
        "events": events,
        "summary": _engine.to_json(exec_ctx, workflow),
    }


@router.post("/save")
async def api_workflow_save(body: WorkflowSaveRequest):
    """保存工作流定义。"""
    notebook_id = body.notebook_id
    if not notebook_id:
        raise HTTPException(400, "需要 notebook_id")

    nb = FileNotebook(notebook_id)
    wf_path = nb.root / "workflow.json"
    wf_path.write_text(json.dumps(body.workflow, ensure_ascii=False, indent=2))
    return {"saved": True, "path": str(wf_path)}


@router.get("/load/{notebook_id}")
async def api_workflow_load(nb: NotebookDep):
    """加载已保存的工作流定义。"""
    wf_path = nb.root / "workflow.json"
    if not wf_path.exists():
        return {"workflow": None}
    return {"workflow": json.loads(wf_path.read_text())}


@router.post("/{notebook_id}/pin-data")
async def api_workflow_pin_data(notebook_id: str, body: dict):
    """设置 pinData — 固定节点输出数据用于调试。"""
    nb = FileNotebook(notebook_id)
    pin_path = nb.root / "pin_data.json"
    # 合并
    existing = {}
    if pin_path.exists():
        existing = json.loads(pin_path.read_text())
    existing.update(body)
    pin_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2))
    return {"pinned_nodes": list(existing.keys())}


@router.get("/{notebook_id}/pin-data")
async def api_workflow_get_pin_data(notebook_id: str):
    """获取 pinData。"""
    nb = FileNotebook(notebook_id)
    pin_path = nb.root / "pin_data.json"
    if not pin_path.exists():
        return {"pin_data": {}}
    return {"pin_data": json.loads(pin_path.read_text())}
